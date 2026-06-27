;=============================================================================
; game_vm_fetch.asm  --  read the bytecode stream + move the program counter.
;
;   The bytecode lives in VRAM and is read through a MEMAC-B window. These are the
;   low-level helpers every opcode leans on:
;     m_vm_w     MACRO: fetch a big-endian 16-bit operand (inlined at each call
;                site for speed -- condjmp alone fires it ~24-28x per frame).
;     vm_w       the jsr-able form of the same, for the one cold caller (game_text).
;     vm_setpc   point the bytecode pointer at a given 16-bit PC (used by jumps).
;     vm_save_pc derive the logical PC back from the running window pointer (done
;                on yield / call / return, since per-byte PC tracking was dropped).
;     vm_page    resolve a page argument (0..3 / cur2 / cur3) to a physical page.
;
;   ORDER MATTERS: the opcode and draw files below EXPAND the m_vm_w macro, and a
;   MADS macro must be defined before use -- so game_vm.asm icl's THIS file first.
;
;   Part of the game_vm split.
;=============================================================================

;=============================================================================
; fetch helpers
;=============================================================================
; m_vm_w : INLINE fetch of a big-endian word -> vm_s2 = high, vm_s1 = low (A = low).
;   Replaces the old `m_vm_w` routine at every call site to drop the 12-cyc jsr/rts
;   (op_condjmp alone fires ~24-28x/frame, each calling this 1-2x). The expansion is
;   BYTE-IDENTICAL to the old routine body, so register/flag/memory state is unchanged
;   (X/A preserved -- mfetch uses Y only; pl_wrap does pha/pla) -- only jsr/rts is gone.
.macro m_vm_w
        mfetch
        sta vm_s2                   ; high byte first (big-endian)
        mfetch
        sta vm_s1                   ; low byte (A = low on exit)
.endm

; vm_w : the jsr-able form, kept for the COLD caller (game_text.asm op_drawstring);
;   the hot game_vm.asm opcodes use the m_vm_w macro inline instead.
vm_w    m_vm_w
        rts

; vm_setpc : seek the bytecode pointer to the 16-bit PC in vm_s2:vm_s1.
;   pl_addr = PLAY_BASE + pc ; bytecode < 64 KB so pl_hi is constant.
vm_setpc
        lda vm_s1
        sta pl_lo
        lda vm_s2
        sta pl_mid
        jmp set_pl_ptr              ; sync pointer + bank, then return to caller

; vm_save_pc : derive the logical PC (pl_lo/pl_mid) from the running window pointer
;   (pl_bank/pl_whi/pl_wlo), for saving on yield/remove/call (aw3 drops the per-byte
;   PC). Inverse of set_pl_ptr; uses tmp_lo so vm_s1/vm_s2 (a call target) survive.
vm_save_pc
        lda pl_wlo
        sta pl_lo
        lda pl_bank
        and #$7F
        sec
        sbc #PLAY_BANK0             ; bank index 0..3
        tax
        lda pl_whi
        and #$3F
        ora pf_bank_hi,x            ; | bank_idx<<6 (LUT, was a 6x asl chain)
        sta pl_mid
        rts

;=============================================================================
; vm_page : resolve a page argument in A -> physical page index in A.
;   p<=3 : p ; 0xFF : cur3 ; 0xFE : cur2 ; else : 0   (= aw_sim.VM.page)
;=============================================================================
vm_page
        cmp #4
        bcc ?ret
        cmp #$FF
        beq ?c3
        cmp #$FE
        beq ?c2
        lda #0
        rts
?c3     lda vm_cur3
        rts
?c2     lda vm_cur2
?ret    rts

