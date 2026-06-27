;=============================================================================
; game_vm_ops1.asm  --  opcode handlers 0x00-0x0A : the "compute & decide" group.
;
;   The VM instructions that do maths and control flow: load a constant into a
;   variable, copy / add / subtract between the 256 variables, call / return / jump,
;   yield the thread, install another thread, decrement-and-branch (djnz) and the
;   signed conditional jump (condjmp). Each handler loops back to the fetch loop in
;   game_vm_sched.asm -- via `jmp vm_fetch` (these never end the thread slice), EXCEPT
;   op_yield, which sets vm_goto and uses `jmp vm_cont` (see the note at vm_cont).
;
;   The 16-bit operand fetch (m_vm_w) comes from game_vm_fetch.asm, icl'd before
;   this. Part of the game_vm split.
;=============================================================================

;=============================================================================
; opcode handlers : each loops back with `jmp vm_fetch` (the common "continue"
; tail), or `jmp vm_cont` for the few that end the thread slice (set vm_goto).
;=============================================================================
op_movconst                          ; 0x00 : var[b()] = sw()
        mfetch
        tax
        m_vm_w
        lda vm_s1
        sta var_lo,x
        lda vm_s2
        sta var_hi,x
        jmp vm_fetch

op_mov                               ; 0x01 : var[d] = var[s]
        mfetch
        sta vm_d
        mfetch
        tax
        lda var_lo,x
        ldy vm_d
        sta var_lo,y
        lda var_hi,x
        sta var_hi,y
        jmp vm_fetch

op_add                               ; 0x02 : var[d] += var[s]
        mfetch
        sta vm_d
        mfetch
        tax
        ldy vm_d
        lda var_lo,y
        clc
        adc var_lo,x
        sta var_lo,y
        lda var_hi,y
        adc var_hi,x
        sta var_hi,y
        jmp vm_fetch

op_addconst                          ; 0x03 : var[v] += sw()
        mfetch
        sta vm_d
        m_vm_w
        ldx vm_d
        lda var_lo,x
        clc
        adc vm_s1
        sta var_lo,x
        lda var_hi,x
        adc vm_s2
        sta var_hi,x
        jmp vm_fetch

op_call                              ; 0x04 : push PC ; PC = w()
        m_vm_w                    ; target -> vm_s1/vm_s2 ; pointer now past the operand
        jsr vm_save_pc              ; pl_lo/pl_mid = return address (vm_s1/vm_s2 survive)
        ldx vm_ssp
        lda pl_lo
        sta vstk_lo,x
        lda pl_mid
        sta vstk_hi,x
        inx
        stx vm_ssp
        jsr vm_setpc
        jmp vm_fetch

op_ret                               ; 0x05 : PC = pop
        ldx vm_ssp
        dex
        lda vstk_lo,x
        sta pl_lo
        lda vstk_hi,x
        sta pl_mid
        stx vm_ssp
        jsr set_pl_ptr              ; resync after the PC jump
        jmp vm_fetch

op_yield                             ; 0x06 : end the thread slice
        lda #1
        sta vm_goto
        jmp vm_cont

op_jmp                               ; 0x07 : PC = w()
        m_vm_w
        jsr vm_setpc
        jmp vm_fetch

op_install                           ; 0x08 : treq[b()] = w()
        mfetch
        sta vm_d
        m_vm_w
        ldx vm_d
        lda vm_s1
        sta treq_lo,x
        lda vm_s2
        sta treq_hi,x
        lda #1
        sta req_any                 ; a request is pending -> next apply scan runs
        jmp vm_fetch

op_djnz                              ; 0x09 : if --var[v] != 0 : PC = w()
        mfetch
        sta vm_d
        tax
        lda var_lo,x
        sec
        sbc #1
        sta var_lo,x
        lda var_hi,x
        sbc #0
        sta var_hi,x
        m_vm_w
        ldx vm_d
        lda var_lo,x
        ora var_hi,x
        beq ?nojmp
        jsr vm_setpc
?nojmp  jmp vm_fetch

op_condjmp                           ; 0x0A : conditional jump (signed compare)
        mfetch
        sta vm_sub
        mfetch
        sta vm_d                    ; v : a = var[v]
        lda vm_sub
        and #$80
        beq ?not80
        mfetch                 ; b2 = var[b()]
        tax
        lda var_lo,x
        sta vm_b2lo
        lda var_hi,x
        sta vm_b2hi
        jmp ?havb
?not80  lda vm_sub
        and #$40
        beq ?byte
        m_vm_w                    ; b2 = sw() (word)
        lda vm_s1
        sta vm_b2lo
        lda vm_s2
        sta vm_b2hi
        jmp ?havb
?byte   jsr pl_byte                 ; b2 = b() (unsigned byte)
        sta vm_b2lo
        lda #0
        sta vm_b2hi
?havb   m_vm_w                    ; dst = w()
        lda vm_s1
        sta vm_dstlo
        lda vm_s2
        sta vm_dsthi
        ; diff = a - b2 (signed 16-bit) ; derive eq and signed-lt
        ldx vm_d
        sec
        lda var_lo,x
        sbc vm_b2lo
        sta vm_s1                   ; diff lo (for eq)
        lda var_hi,x
        sbc vm_b2hi
        sta vm_s2                   ; diff hi (for eq)
        bvc ?nov
        eor #$80                    ; signed correction
?nov    and #$80                    ; bit7 = signed (a < b2)
        sta vm_op                   ; vm_op = $80 if a<b2 else 0  (lt flag)
        lda vm_s1
        ora vm_s2
        beq ?iseq
        lda #0                      ; not equal
        beq ?eqset
?iseq   lda #$80
?eqset  sta vm_d                    ; vm_d = $80 if equal else 0  (eq flag; v no longer needed)
        ; compute the "take" flag in A bit7 (short branches + jmp; the cmp-chain
        ; with far bmi/bpl to ?take overflowed the branch range).
        lda vm_sub
        and #7
        cmp #0
        bne ?n0
        lda vm_d                    ; == : eq
        jmp ?decide
?n0     cmp #1
        bne ?n1
        lda vm_d                    ; != : !eq
        eor #$80
        jmp ?decide
?n1     cmp #2
        bne ?n2
        lda vm_op                   ; >  : !(lt | eq)
        ora vm_d
        eor #$80
        jmp ?decide
?n2     cmp #3
        bne ?n3
        lda vm_op                   ; >= : !lt
        eor #$80
        jmp ?decide
?n3     cmp #4
        bne ?n4
        lda vm_op                   ; <  : lt
        jmp ?decide
?n4     lda vm_op                   ; <= : lt | eq
        ora vm_d
?decide and #$80
        beq ?notake
        lda vm_dstlo
        sta vm_s1
        lda vm_dsthi
        sta vm_s2
        jsr vm_setpc
?notake jmp vm_fetch

