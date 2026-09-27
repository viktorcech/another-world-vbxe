; op_memlist -- COLD (fires only on resource loads / part switches, 0 execs/frame
; in the whole-game profile), so it lives OUT of the $2000 hot chain: dispatched
; through vm_optab (a jmp -- the cold path pays nothing for the distance) and
; assembled here at game_text's end inside the guarded $0900-$0BBF region.
; (6502-cycles-layout: a rare block that ends in its own jmp/rts always pays.)

op_memlist                           ; 0x19 : resource load / part switch
.if 1
        mfetch0x sfw29                    ; num -> vm_s2:vm_s1
sfr29
        sta vm_s2
        mfetch0x sfw30
sfr30
        sta vm_s1
.else
        m_vm_w0                    ; num -> vm_s2:vm_s1
.endif
        lda vm_s2
        cmp #$3E                    ; 0x3E80 high byte
        bcc ?mlbmp                  ; num < 0x3E00 -> resource load -> bitmap?
        bne ?mlpart                 ; num high > 0x3E -> part switch
        lda vm_s1
        cmp #$80
        bcc ?mlbmp                  ; 0x3E00..0x3E7F -> not a part -> bitmap?
?mlpart lda vm_s1                   ; request a part switch (applied before next pass)
        sta vm_next_lo
        lda vm_s2
        sta vm_next_hi
        lda #1
        sta vm_switch
        jmp vm_exit                 ; end this thread slice (save the PC, rts)
.if 1
sfw29   jsr pl_wrap
        jmp sfr29
sfw30   jsr pl_wrap
        jmp sfr30
.endif
        ; --- a sub-16000 resource: if it's a known background BITMAP, stream it to
        ;     page 0 (luxe etc.); other sub-16000 loads (sounds) stay a no-op.
        ;     (unique ?ml* labels -- a plain ?done here mis-binds op_shl's beq ?done) ---
?mlbmp  ldx #0
?mlbl   cpx #GAME_NBMP
        bcs ?mldone                 ; not in the bitmap table -> ignore
        lda atr_bmp_num_lo,x
        cmp vm_s1
        bne ?mlbn
        lda atr_bmp_num_hi,x
        cmp vm_s2
        beq ?mlfound
?mlbn   inx
        bne ?mlbl
?mlfound txa
        jsr load_bitmap             ; A = bitmap index -> stream to VRAM page 0
?mldone jmp vm_fetch

        ert *>$0BBF                 ; must stay inside the game_text region
