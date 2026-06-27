;=============================================================================
; game_vm_sched.asm  --  the cooperative thread scheduler (the VM's heartbeat).
;
;   AW runs up to 64 lightweight threads that voluntarily yield (it never pre-empts
;   them). vm_run_frame is ONE scheduler pass: honour any pending part switch, read
;   input, install / pause / remove the threads that asked to be, then run every
;   active thread once. vm_run_thread runs a single thread -- fetch an opcode,
;   dispatch to its handler (the self-modifying `jmp` driven by game_vm_optab.asm),
;   repeat -- until the thread yields or removes itself, then saves its program
;   counter so it picks up from there next frame.
;
;   Part of the game_vm split.
;=============================================================================

;=============================================================================
; vm_run_frame : one scheduler pass -- read input, apply thread requests, then
; run every active, non-paused thread until it yields.  (= game_sim.GameVM.step)
;=============================================================================
vm_run_frame
        lda vm_switch               ; a pending part switch loads before this pass
        beq ?nosw
        lda #0
        sta vm_switch
        ; entering the access-code (16008) by 'C' -> snapshot the current scene so ESC can
        ; RESUME it (skip when this switch is itself the ESC-return).
        lda code_return
        bne ?dosw
        lda vm_next_hi
        cmp #>16008
        bne ?dosw
        lda vm_next_lo
        cmp #<16008
        bne ?dosw
        jsr snapshot_state
        lda #0                      ; pages 0-3 -> the VRAM snapshot slots (16008
        sta vm_s1                   ;   clears/draws over every page)
        jsr pages_xfer
?dosw   lda code_return             ; ESC return : bring the saved LR pages back FIRST
        beq ?nopr                   ;   -- load_part below restreams poly/code/v2/sfx
        lda #1                      ;   OVER the snapshot slots
        sta vm_s1
        jsr pages_xfer
?nopr   lda vm_next_lo              ; index = part - GAME_FIRST_PART (parts 16000..16008)
        sec
        sbc #<GAME_FIRST_PART
        tax
        jsr load_part               ; overwrite VRAM banks with the new part
        lda code_return            ; ESC-return -> restore the saved scene (resume), else reset
        beq ?reset
        lda #0
        sta code_return
        jsr restore_state
        jmp ?nosw
?reset  jsr vm_reset_threads        ; threads reset, variables persist
?nosw   jsr vm_update_input
        jsr vm_check_code           ; 'C' -> request the password (16008) screen
        lda req_any                 ; nothing posted since the last apply scan ->
        beq ?ardone                 ;   skip the whole 64-thread request loop
        lda #0
        sta req_any
        ldx #0                      ; apply tpause_req + treq
?aloop  lda tpreq,x
        cmp #$FF
        beq ?notpr
        sta tpause,x
        lda #$FF
        sta tpreq,x
?notpr  lda treq_hi,x
        cmp #$FF
        bne ?settpc                 ; real PC
        lda treq_lo,x
        cmp #$FF
        beq ?notreq                 ; $FFFF = none
        lda #$FF                    ; $FFFE = remove -> INACTIVE
        sta tpc_hi,x
        jmp ?clrreq
?settpc lda treq_lo,x
        sta tpc_lo,x
        lda treq_hi,x
        sta tpc_hi,x
?clrreq lda #$FF
        sta treq_lo,x
        sta treq_hi,x
?notreq inx
        cpx #64
        bne ?aloop
?ardone ldx #0                      ; run active threads
?rloop  lda tpc_hi,x                ; INACTIVE first: the common idle case (only
        cmp #$FF                    ;   ~5-16 of 64 threads are installed) -> the
        beq ?rnext                  ;   cheap test short-circuits the scan
        lda tpause,x
        bne ?rnext
        txa                         ; save the loop index on the hardware stack --
        pha                         ; vm_run_thread clobbers vm_s1/vm_s2 (vm_w scratch)
        jsr vm_run_thread           ; X = thread index
        pla
        tax
        lda vm_switch               ; a thread asked to switch part -> end the pass now
        bne ?rdone
        lda vm_running
        beq ?rdone                  ; no active threads
?rnext  inx
        cpx #64
        bne ?rloop
?rdone  rts

;=============================================================================
; vm_run_thread : run thread X until op_yield / op_remove.
;=============================================================================
vm_run_thread
        stx vm_t
        lda tpc_lo,x                ; PC = tpc[X]  (pl_byte's running pointer)
        sta pl_lo
        lda tpc_hi,x
        sta pl_mid
        jsr set_pl_ptr              ; sync the running window pointer + bank
        lda #0
        sta vm_goto
        sta vm_rem
        sta vm_ssp
vm_fetch
        jsr pl_byte                 ; A = opcode (re-owns the bank; may follow a draw)
        cmp #$80
        bcc ?n80
        jsr draw_bg
        jmp vm_fetch
?n80    cmp #$40
        bcc ?op
        jsr draw_sprite
        jmp vm_fetch
?op     cmp #27                     ; opcodes 0x00-0x1A valid; >=0x1B = bad PC / garbage
        bcc *+5                     ; valid -> skip; else halt the thread (never wild-jump)
        jmp op_remove
        asl @                       ; opcode * 2 -> jump-table index
        tay
        lda vm_optab,y
        sta vm_disp+1               ; SMC dispatch: patch the jmp operand, then `jmp abs`
        lda vm_optab+1,y            ;   (3 cyc) vs `jmp (abs)` (5) -> -2 cyc/opcode, and no
        sta vm_disp+2               ;   vm_jmp RAM cell needed (frees RAMB+93/+94).
vm_disp jmp $FFFF
; vm_cont / vm_fetch dispatch tail. vm_goto starts 0 (vm_run_thread above) and is
; set to 1 ONLY by op_yield / op_remove / op_memlist's part-switch -- and once set,
; the thread always exits here. So for EVERY other opcode (and after a draw) vm_goto
; is provably 0: those handlers `jmp vm_fetch` directly (3 cyc), skipping this
; re-read of vm_goto -- saving ~6 cyc/opcode vs routing through vm_cont. Only the
; three slice-ending handlers `jmp vm_cont`, to save the PC (or skip it if the
; thread removed itself) and rts back to the scheduler.
vm_cont
        lda vm_goto
        beq vm_fetch
        lda vm_rem
        bne ?nosave                 ; removed -> tpc[t] already INACTIVE
        jsr vm_save_pc              ; derive the PC from the running pointer (aw3)
        ldx vm_t                    ; save PC = pl_mid:pl_lo -> tpc[t]
        lda pl_lo
        sta tpc_lo,x
        lda pl_mid
        sta tpc_hi,x
?nosave rts

