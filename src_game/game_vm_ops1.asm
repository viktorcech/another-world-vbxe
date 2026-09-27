;=============================================================================
; game_vm_ops1.asm  --  opcode handlers 0x00-0x0A : the "compute & decide" group.
;
;   The VM instructions that do maths and control flow: load a constant into a
;   variable, copy / add / subtract between the 256 variables, call / return / jump,
;   install another thread, decrement-and-branch (djnz) and the signed conditional
;   jump (condjmp). Each handler loops back to the fetch loop in game_vm_sched.asm
;   via `jmp vm_fetch` (none of these end the thread slice). op_yield lives in
;   game_vm_sched.asm now: it IS vm_exit (save the PC, rts) -- the old vm_goto /
;   vm_cont plumbing is gone (2026-07-02 fps wave).
;
;   The 16-bit operand fetch (m_vm_w) comes from game_vm_fetch.asm, icl'd before
;   this. Part of the game_vm split.
;=============================================================================

;=============================================================================
; opcode handlers : each loops back with `jmp vm_fetch` (the common "continue"
; tail); the slice-ending ones (yield / remove / memlist) exit via vm_exit / rts.
;=============================================================================
op_movconst                          ; 0x00 : var[b()] = sw()
.if 1
        mfetch0x sfw33
sfr33
.else
        mfetch0
.endif
        tax
.if 1
        mfetch0x ?vw1               ; big-endian: the high byte comes first -- each
?vr1    sta var_hi,x                ;   byte goes straight to the variable (no vm_s2)
        mfetch0x ?vw2
?vr2    sta var_lo,x
.else
        m_vm_w0
        sta var_lo,x
        lda vm_s2
        sta var_hi,x
.endif
        jmp vm_fetch
.if 1
sfw33   jsr pl_wrap
        jmp sfr33
?vw1    jsr pl_wrap
        jmp ?vr1
?vw2    jsr pl_wrap
        jmp ?vr2
.endif

op_mov                               ; 0x01 : var[d] = var[s]
.if 1
        mfetch0x ?mw1
?mr1    tax                         ; X = d (mfetch uses Y only, X survives it)
        mfetch0x ?mw2
?mr2    tay                         ; Y = s
        lda var_lo,y
        sta var_lo,x
        lda var_hi,y
        sta var_hi,x
        jmp vm_fetch
?mw1    jsr pl_wrap
        jmp ?mr1
?mw2    jsr pl_wrap
        jmp ?mr2

op_add                               ; 0x02 : var[d] += var[s]
.if 1
        mfetch0x sfw34
sfr34
.else
        mfetch0
.endif
        tax                         ; X = d
.if 1
        mfetch0x sfw35
sfr35
.else
        mfetch0
.endif
        tay                         ; Y = s
        lda var_lo,x
        clc
        adc var_lo,y
        sta var_lo,x
        lda var_hi,x
        adc var_hi,y
        sta var_hi,x
        jmp vm_fetch
.if 1
sfw34   jsr pl_wrap
        jmp sfr34
sfw35   jsr pl_wrap
        jmp sfr35
.endif

op_addconst                          ; 0x03 : var[v] += sw()
        mfetch0x ?aw1
?ar1    tax                         ; X = v (survives m_vm_w)
        mfetch0x ?aw2               ; m_vm_w0 with out-of-line wraps
?ar2    sta vm_s2
        mfetch0x ?aw3
?ar3    sta vm_s1                   ; (A = low byte)
        clc
        adc var_lo,x
.else
        mfetch0
        sta vm_d
        mfetch0
        tax
        lda var_lo,x
        ldy vm_d
        sta var_lo,y
        lda var_hi,x
        sta var_hi,y
        jmp vm_fetch

op_add                               ; 0x02 : var[d] += var[s]
        mfetch0
        sta vm_d
        mfetch0
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
        mfetch0
        sta vm_d
        m_vm_w0
        ldx vm_d
        lda var_lo,x
        clc
        adc vm_s1
.endif
        sta var_lo,x
        lda var_hi,x
        adc vm_s2
        sta var_hi,x
        jmp vm_fetch
.if 1
?aw1    jsr pl_wrap
        jmp ?ar1
?aw2    jsr pl_wrap
        jmp ?ar2
?aw3    jsr pl_wrap
        jmp ?ar3
.endif

op_call                              ; 0x04 : push PC ; PC = w()
.if 1
        mfetch0x ?kw1               ; m_vm_w0 (target -> vm_s2/vm_s1) with
?kr1    sta vm_s2                   ;   out-of-line wraps
        mfetch0x ?kw2
?kr2    sta vm_s1
.else
        m_vm_w0                    ; target -> vm_s1/vm_s2 ; pointer now past the operand
.endif
.if 1
        m_save_pc                   ; pl_lo/pl_mid = return address, A = pl_mid
        ldx vm_ssp
        sta vstk_hi,x
        lda pl_lo
        sta vstk_lo,x
.else
        jsr vm_save_pc              ; pl_lo/pl_mid = return address (vm_s1/vm_s2 survive)
        ldx vm_ssp
        lda pl_lo
        sta vstk_lo,x
        lda pl_mid
        sta vstk_hi,x
.endif
        inx
        stx vm_ssp
.if 1
        lda vm_s1                   ; PC = target
        ldx vm_s2
        jmp vm_goto
.else
        jsr vm_setpc
        jmp vm_fetch
.endif
.if 1
?kw1    jsr pl_wrap
        jmp ?kr1
?kw2    jsr pl_wrap
        jmp ?kr2
.endif

op_ret                               ; 0x05 : PC = pop
.if 1
        ldy vm_ssp                  ; (Y indexes the call stack: X carries the hi byte)
        dey
        sty vm_ssp
        ldx vstk_hi,y
        lda vstk_lo,y
        jmp vm_goto
.else
        ldx vm_ssp
        dex
        lda vstk_lo,x
        sta pl_lo
        lda vstk_hi,x
        sta pl_mid
        stx vm_ssp
        jsr set_pl_ptr              ; resync after the PC jump
        jmp vm_fetch
.endif

; (op_yield -- 0x06 -- is vm_exit in game_vm_sched.asm: the optab points there)

op_jmp                               ; 0x07 : PC = w()
.if 1
.if 1
        mfetch0x sfw32                     ; hi (big-endian first) -> X
sfr32
.else
        mfetch0                     ; hi (big-endian first) -> X
.endif
        tax
.if 1
        mfetch0x sfw31                     ; lo -> A
sfr31
.else
        mfetch0                     ; lo -> A
.endif
        jmp vm_goto
.if 1
sfw32   jsr pl_wrap
        jmp sfr32
.endif
.if 1
sfw31   jsr pl_wrap
        jmp sfr31
.endif
.else
        m_vm_w0
        jsr vm_setpc
        jmp vm_fetch
.endif

op_install                           ; 0x08 : treq[b()] = w()
.if 1
        mfetch0x sfw36
sfr36
.else
        mfetch0
.endif
.if 1
        tax                         ; X = thread (survives mfetch)
        mfetch0                     ; high byte first, straight into the request
        sta treq_hi,x
        mfetch0
        sta treq_lo,x
.else
        sta vm_d
        m_vm_w0
        ldx vm_d
        lda vm_s1
        sta treq_lo,x
        lda vm_s2
        sta treq_hi,x
.endif
        lda #1
        sta req_any                 ; a request is pending -> next apply scan runs
        jmp vm_fetch
.if 1
sfw36   jsr pl_wrap
        jmp sfr36
.endif

op_djnz                              ; 0x09 : if --var[v] != 0 : PC = w()
        mfetch0
.if 1
        tax                         ; X = v (survives m_vm_w below)
.else
        sta vm_d
        tax
.endif
.if 1
        lda var_lo,x                ; var[v]-- : 16-bit dec (hi only when lo was 0)
        bne ?dlo
        dec var_hi,x
?dlo    dec var_lo,x                ; Z = (lo == 0): the decision needs no reload
        bne ?dgo
        lda var_hi,x
        bne ?dgo
        inc pl_wlo                  ; var = 0 : no jump -- step over the target word
        beq ?dw1                     ;   unread (the pointer walk mfetch does)
?ds2     inc pl_wlo
        beq ?dw2
        jmp vm_fetch
?dgo     mfetch0x ?dw3               ; PC = w() -> A = lo, X = hi (Y = 0 still)
?dr3
        tax
        mfetch0x ?dw4
?dr4
        jmp vm_goto
?dw1     jsr pl_wrap
        jmp ?ds2
?dw2     jsr pl_wrap
        jmp vm_fetch
?dw3     jsr pl_wrap
        jmp ?dr3
?dw4     jsr pl_wrap
        jmp ?dr4
.else
        lda var_lo,x
        sec
        sbc #1
        sta var_lo,x
        lda var_hi,x
        sbc #0
        sta var_hi,x
        m_vm_w0
        ldx vm_d
        lda var_lo,x
        ora var_hi,x
        beq ?nojmp
        jsr vm_setpc
?nojmp  jmp vm_fetch
.endif

.if 1
        nocross op_condjmp, cj_b, cj_w
.endif
op_condjmp                           ; 0x0A : conditional jump (signed compare)
.if 1
; The decision is one table read: index = op*4 + lt*2 + eq (op = sub & 7; 0 ==, 1 !=,
; 2 >, 3 >=, 4 <, 5-7 <=), instead of flags materialised in RAM + a compare chain.
        mfetch0x ?cw1               ; sub-op: bit 7 = var operand, bit 6 = word operand
?cr1    sta vm_sub
        tax
        mfetch0x ?cw2
?cr2    sta vm_d                    ; v : a = var[v]
        txa                         ; N = bit 7 of the sub-op
        bmi ?is80                   ; (the rarer operand forms out of line)
cj_b    bit vm_sub                  ; V = bit 6: a word operand
        bvs ?isw
        mfetch0x ?cw3               ; b2 = b() (unsigned byte)
?cr3    sta vm_b2lo
        sty vm_b2hi                 ; = 0 (Y = 0 after mfetch)
?havb   ldx vm_d                    ; diff = a - b2 (signed 16-bit)
        sec
        lda var_lo,x
        sbc vm_b2lo
        tay                         ; Y = diff lo
        lda var_hi,x
        sbc vm_b2hi
        sta vm_d                    ; diff hi
        bvs ?ovf
?nov    asl @                       ; C = lt
        lda vm_sub
        and #7
        rol @                       ; op*2 + lt
        asl @                       ; op*4 + lt*2  (op*4 < 32: C = 0)
        tax
        tya
        ora vm_d
        beq ?eq
?ne     lda cj_take,x               ; (table in the $1DC0 cold gap, page-safe)
        bne ?go
        inc pl_wlo                  ; not taken: step over the target word unread
        beq ?w1                     ;   (the same pointer walk mfetch does)
?s2     inc pl_wlo
        beq ?w2
        jmp vm_fetch
?eq     inx                         ; + eq (moved up: bvs/beq reach shrank when the
        jmp ?ne                     ;   wrap stubs below were added)
?ovf    eor #$80                    ; bit 7 = signed (a < b2)
        jmp ?nov
?cw1    jsr pl_wrap                 ; out-of-line window wraps (rare): back to the
        jmp ?cr1                    ;   byte after the beq that came here
?cw2    jsr pl_wrap
        jmp ?cr2
?cw3    jsr pl_wrap
        jmp ?cr3
?cw4    jsr pl_wrap
        jmp ?cr4
?cw5    jsr pl_wrap
        jmp ?cr5
?cw6    jsr pl_wrap
        jmp ?cr6
?is80   mfetch0x ?cw4               ; b2 = var[b()]
?cr4    tax
        lda var_lo,x
        sta vm_b2lo
        lda var_hi,x
        sta vm_b2hi
        jmp ?havb
cj_w
?isw    mfetch0x ?cw7               ; b2 = sw() (word): m_vm_w0 with out-of-line
?cr7    sta vm_s2                   ;   wraps; A = low byte at the end
        mfetch0x ?cw8
?cr8    sta vm_s1
        sta vm_b2lo
        lda vm_s2
        sta vm_b2hi
        jmp ?havb
?go     ldy #0                      ; taken: PC = dst = w() -> A = lo, X = hi
        mfetch0x ?cw5               ;   (Y held the diff: the fetch wants Y = 0)
?cr5    tax
        mfetch0x ?cw6
?cr6    jmp vm_goto
?w1     jsr pl_wrap
        jmp ?s2
?w2     jsr pl_wrap
        jmp vm_fetch
?cw7    jsr pl_wrap
        jmp ?cr7
?cw8    jsr pl_wrap
        jmp ?cr8
.else
        mfetch0
        sta vm_sub
.if 1
        tax                         ; (skill pass: keep the sub-op in X across the
        mfetch0                      ;   operand fetch instead of reloading it)
        sta vm_d                    ; v : a = var[v]
        txa
.else
        mfetch0
        sta vm_d                    ; v : a = var[v]
        lda vm_sub
.endif
        and #$80
        beq ?not80
        mfetch0                 ; b2 = var[b()]
        tax
        lda var_lo,x
        sta vm_b2lo
        lda var_hi,x
        sta vm_b2hi
        jmp ?havb
?not80  lda vm_sub
        and #$40
        beq ?byte
.if 1
        m_vm_w0                    ; b2 = sw() (word) ; A = low byte
.else
        m_vm_w0                    ; b2 = sw() (word)
        lda vm_s1
.endif
        sta vm_b2lo
        lda vm_s2
        sta vm_b2hi
        jmp ?havb
?byte   mfetch0                      ; b2 = b() (unsigned byte; mid-opcode -> no
                                    ;   bank re-own needed, same as the operand
                                    ;   fetches above -- was `jsr pl_byte`)
        sta vm_b2lo
.if 1
        sty vm_b2hi                 ; = 0 (Y = 0 after mfetch)
?havb   m_vm_w0                    ; dst = w() -> stays in vm_s1/vm_s2 for vm_setpc
        ; (skill pass: the old code copied dst to vm_dstlo/hi, used vm_s1/s2 as the
        ;  diff scratch and copied dst back before vm_setpc -- 4 round-trips. The diff
        ;  now lives in Y (lo) / vm_d (hi; v is only needed as X from here on).)
.else
        lda #0
        sta vm_b2hi
?havb   m_vm_w0                    ; dst = w()
        lda vm_s1
        sta vm_dstlo
        lda vm_s2
        sta vm_dsthi
.endif
        ; diff = a - b2 (signed 16-bit) ; derive eq and signed-lt
        ldx vm_d
        sec
        lda var_lo,x
        sbc vm_b2lo
.if 1
        tay                         ; diff lo (for eq)
        lda var_hi,x
        sbc vm_b2hi
        sta vm_d                    ; diff hi (for eq)
        bvc ?nov
        eor #$80                    ; signed correction
?nov    and #$80                    ; bit7 = signed (a < b2)
        sta vm_op                   ; vm_op = $80 if a<b2 else 0  (lt flag)
        tya
        ora vm_d
.else
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
.endif
        beq ?iseq
        lda #0                      ; not equal
        beq ?eqset
?iseq   lda #$80
?eqset  sta vm_d                    ; vm_d = $80 if equal else 0  (eq flag; v no longer needed)
        ; compute the "take" flag in A bit7 (short branches + jmp; the cmp-chain
        ; with far bmi/bpl to ?take overflowed the branch range).
        lda vm_sub
.if 1
        and #7                      ; (Z/N already set by the and: no `cmp #0`)
.else
        and #7
        cmp #0
.endif
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
.if 1
        jsr vm_setpc                ; dst is still in vm_s1/vm_s2
.else
        lda vm_dstlo
        sta vm_s1
        lda vm_dsthi
        sta vm_s2
        jsr vm_setpc
.endif
?notake jmp vm_fetch
.endif

