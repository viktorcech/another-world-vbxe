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
;   PERF (2026-07-02 fps wave -- see docs/SESSION-2026-07-02-fps-wave.md):
;     * the opcode fetch is INLINED at vm_fetch (pl_byte's body: bank re-own +
;       windowed read); the old `jsr pl_byte` + rts cost 12 cyc on EVERY opcode.
;     * dispatch goes through a 64-entry SPLIT lo/hi table (vm_oplo/vm_ophi,
;       game_vm_optab.asm) indexed by the opcode DIRECTLY -- no `asl`, no range
;       check (entries $1B-$3F all point at op_remove, the old bad-PC behaviour).
;     * the draw opcodes test first ($80+ = draw_bg, $40+ = draw_sprite) and are
;       TAIL-CALLED (jmp, not jsr) -- do_draw ends with `jmp vm_fetch`.
;     * vm_cont / vm_goto / vm_rem are GONE: the three slice-ending handlers jump
;       straight to vm_exit (save the PC, rts) or rts themselves (op_remove), so
;       every other opcode never pays a slice-end test. op_yield IS vm_exit (the
;       optab points opcode 6 straight at it).
;     * vm_maxt ($BA, freed by vm_goto) = watermark: highest thread index ever
;       installed + 1. The run loop scans only 0..vm_maxt-1 instead of all 64
;       (AW parts use the low thread slots; typically 5-16 are ever touched).
;       Raised at the apply scan (?settpc) + snapshot-restore; reset by
;       vm_reset_threads. Over-scan is harmless, under-scan never happens.
;   Old cost ~74 cyc dispatch overhead per opcode; new ~53 (-21). Output-identical.
;
;   Part of the game_vm split.
;=============================================================================

;=============================================================================
; vm_run_frame : one scheduler pass -- read input, apply thread requests, then
; run every active, non-paused thread until it yields.  (= game_sim.GameVM.step)
;=============================================================================
.if 1
        nocross2 vm_run_frame, vm_run_frame, vrf_ns, vrf_a, vrf_done   ; the no-switch hop, the
                                    ;   two 64-thread loops: one page each
.endif
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
vrf_ns
?nosw   jsr vm_update_input
        jsr vm_check_code           ; 'C' -> request the password (16008) screen
        lda req_any                 ; nothing posted since the last apply scan ->
        beq ?ardone                 ;   skip the whole 64-thread request loop
vrf_a
        lda #0
        sta req_any
.if 1
        ldx #63                     ; apply tpause_req + treq, threads 63..0: each
                                    ;   thread's requests are its own, and the watermark
                                    ;   below is a max -- the order does not matter
.else
        ldx #0                      ; apply tpause_req + treq
.endif
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
.if 1
        lda #$FF                    ; $FFFE = remove -> INACTIVE
        sta tpc_hi,x
        bne ?clr2                   ; (A = $FF: always taken, and already the value)
.else
        lda #$FF                    ; $FFFE = remove -> INACTIVE
        sta tpc_hi,x
        jmp ?clrreq
.endif
?settpc lda treq_lo,x
        sta tpc_lo,x
        lda treq_hi,x
        sta tpc_hi,x
        cpx vm_maxt                 ; raise the run-loop watermark: an install may
        bcc ?clrreq                 ;   activate a thread above every previous one
        inx
        stx vm_maxt                 ; watermark = highest installed index + 1
        dex
?clrreq lda #$FF
?clr2   sta treq_lo,x
        sta treq_hi,x
.if 1
?notreq dex
        bpl ?aloop
.else
?notreq inx
        cpx #64
        bne ?aloop
.endif
?ardone ldx #0                      ; run active threads (0 .. vm_maxt-1 only)
?rloop  lda tpc_hi,x                ; INACTIVE first: the common idle case (only
        cmp #$FF                    ;   ~5-16 of 64 threads are installed) -> the
        bne ?ract                   ;   installed (rare) case out of line below
?rnext  inx
.if 1
vmx_2   cpx #1                      ; scan only up to the watermark (this operand IS vm_maxt)
.else
        cpx vm_maxt                 ; scan only up to the watermark
.endif
        bcc ?rloop
        rts                         ; scan done (the common exit: X hit the watermark)
?ract   lda tpause,x                ; installed thread (the rare case, out of line)
        bne ?rnext
        jsr vm_run_thread           ; X = thread index (clobbered; vm_t keeps it)
        ldx vm_t                    ; restore the loop index (cheaper than pha/pla)
        lda vm_switch               ; a thread asked to switch part -> end the pass now
        bne ?rdone
        lda vm_running
        bne ?rnext                  ; still running -> next thread
?rdone
vrf_done rts

;=============================================================================
; vm_run_thread : run thread X until op_yield / op_remove.
;=============================================================================
.if 1
        nocross vm_run_thread, vm_fetch, vf_own  ; the dispatch branches in one page
.endif
vm_run_thread
        stx vm_t
.if 1
        lda #0
        sta vm_ssp
        ldy tpc_lo,x                ; PC = tpc[X] -> X:A = hi:lo, then straight into
        lda tpc_hi,x                ;   vm_goto (no jmp: it follows)
        tax
        tya
; vm_goto : jump the bytecode PC to X:A (hi:lo) and fetch -- set_pl_ptr's work inline,
;   falling into vm_fetch (which re-owns the bank). pl_lo/pl_mid stay unwritten: the
;   PC is derived (m_save_pc) when it is saved.
vm_goto
        sta pl_wlo
        lda poly_bank_lut,x         ; ((hi>>6)+POLY_BANK0)|$80
        clc
        adc #PLAY_BANK0-POLY_BANK0  ; shift base bank $14 -> $18 (bytecode region)
        sta pl_bank
        lda poly_win_lut,x          ; (hi&$3F)|>DATAW
        sta pl_whi
.else
        lda tpc_lo,x                ; PC = tpc[X]  (the running window pointer)
        sta pl_lo
        lda tpc_hi,x
        sta pl_mid
        jsr set_pl_ptr              ; sync the running window pointer + bank
        lda #0
        sta vm_ssp
.endif
; vm_fetch : the opcode fetch + dispatch, fully inlined (the old jsr pl_byte /
; cmp-chain / word-table sequence cost ~74 cyc; this is ~53). The bank re-own is
; needed on the OPCODE fetch only: a draw (set_poly_ptr) may have stolen MEMAC-B
; since the previous opcode; operand fetches inside one opcode use mfetch (no
; re-own -- the sound IRQ restores the register to memb_cur, which IS pl_bank
; between the opcode fetch and the first draw).
vm_fetch
        lda pl_bank                 ; re-own the MEMAC-B bank if a draw took it
        cmp memb_cur                ;   (out of line: the common case falls through)
        bne vf_own
?vfns   ldy #0
        lda (pl_wlo),y              ; A = opcode byte
        inc pl_wlo
        beq ?vfwr                   ; ~1/256 : window page cross
?vfbk   asl @                       ; C = bit 7, N = bit 6: one shift sorts the draws
        bcs ?vfbg                   ; $80-$FF -> draw_bg (tail-call; ends jmp vm_fetch)
        bmi vf_sp                   ; $40-$7F -> draw_sprite
.if 1
        tax                         ; $00-$3F : X = 2 * opcode into the word table, so
        lda vm_optab,x              ;   every handler starts with Y = 0 (its fetches
        sta vm_disp+1               ;   skip the ldy #0: mfetch0). Entries $1B-$3F are
        lda vm_optab+1,x            ;   op_remove.
.else
        tay                         ; $00-$3F -> split-table dispatch, no range check:
        lda vm_oplo,y               ;   entries $1B-$3F are op_remove (bad PC/garbage
        sta vm_disp+1               ;   halts the thread, never wild-jumps)
        lda vm_ophi,y
.endif
        sta vm_disp+2               ; SMC dispatch: `jmp abs` (3) vs `jmp (abs)` (5)
vm_disp jmp $FFFF
?vfwr   jsr pl_wrap                 ; preserves A (the opcode)
        jmp ?vfbk
?vfbg   ror @                       ; A = the opcode again (C = its bit 7)
        jmp draw_bg
vf_sp   lsr @                       ; A = the opcode again (its bit 7 is 0)
        jmp draw_sprite
vf_own  sta memb_cur
        sta VBXE_MEMAC_B
        jmp ?vfns

; vm_exit : the slice-end tail -- save the PC, return to the scheduler. Reached
; ONLY by the slice-ending opcodes: op_yield (the optab maps opcode 6 straight
; HERE -- yield has no body) and op_memlist's part-switch. op_remove rts's on its
; own (tpc[t] is already INACTIVE -> nothing to save). Every other handler loops
; with `jmp vm_fetch` and never pays a slice-end test (the old vm_cont/vm_goto/
; vm_rem plumbing is gone).
op_yield                             ; 0x06 : end the thread slice (= vm_exit)
vm_exit
.if 1
        m_save_pc                   ; derive the PC from the running pointer (aw3)
        ldx vm_t                    ; save PC = pl_mid:pl_lo -> tpc[t] (A = pl_mid)
        sta tpc_hi,x
        lda pl_lo
        sta tpc_lo,x
.else
        jsr vm_save_pc              ; derive the PC from the running pointer (aw3)
        ldx vm_t                    ; save PC = pl_mid:pl_lo -> tpc[t]
        lda pl_lo
        sta tpc_lo,x
        lda pl_mid
        sta tpc_hi,x
.endif
        rts
