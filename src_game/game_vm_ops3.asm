;=============================================================================
; game_vm_ops3.asm  --  opcode handlers 0x11-0x1A : the remaining instructions.
;
;   A grab-bag, together here only because they follow the page ops in AW's opcode
;   numbering: remove (kill this thread), drawstring (DRAWTEXT -- a trampoline into
;   game_text.asm), the bitwise / extra maths ops sub / and / or / shl / shr
;   (siblings of ops1, just later in the table), sound (play a POKEY sample),
;   memlist (load a resource OR switch to another part of the game) and music
;   (a stub that just consumes its operands for now).
;
;   Part of the game_vm split.
;=============================================================================

op_remove                            ; 0x11 : kill this thread (also the handler for
        ldx vm_t                     ;   invalid opcodes $1B-$3F via the optab fill)
        lda #$FF
        sta tpc_hi,x                 ; INACTIVE -> nothing to save; straight back to
        rts                          ;   the scheduler (the old vm_goto/vm_rem is gone)

op_drawstring                        ; 0x12 : DRAWTEXT -> game_text.asm (intro glyph blitter).
        jmp do_drawstring            ;   global label keeps op_remove/op_sub ?-scopes separate.

op_sub                               ; 0x13 : var[d] -= var[s]
        mfetch0
.if 1
        tax                         ; X = d (mfetch uses Y only, X survives it)
.if 1
        mfetch0x sfw9
sfr9
.else
        mfetch0
.endif
        tay                         ; Y = s
        lda var_lo,x
        sec
        sbc var_lo,y
        sta var_lo,x
        lda var_hi,x
        sbc var_hi,y
        sta var_hi,x
        jmp vm_fetch
.if 1
sfw9    jsr pl_wrap
        jmp sfr9
.endif

op_and                               ; 0x14 : var[v] &= w()
.if 1
        mfetch0x sfw1
sfr1
.else
        mfetch0
.endif
        tax                         ; X = v (survives mfetch)
.if 1
        mfetch0x sfw2                     ; high byte first: AND straight into the variable
sfr2
.else
        mfetch0                     ; high byte first: AND straight into the variable
.endif
        and var_hi,x
        sta var_hi,x
.if 1
        mfetch0x sfw3
sfr3
.else
        mfetch0
.endif
        and var_lo,x
        sta var_lo,x
        jmp vm_fetch
.if 1
sfw1    jsr pl_wrap
        jmp sfr1
sfw2    jsr pl_wrap
        jmp sfr2
sfw3    jsr pl_wrap
        jmp sfr3
.endif
.else
        sta vm_d
        mfetch0
        tax
        ldy vm_d
        lda var_lo,y
        sec
        sbc var_lo,x
        sta var_lo,y
        lda var_hi,y
        sbc var_hi,x
        sta var_hi,y
        jmp vm_fetch

op_and                               ; 0x14 : var[v] &= w()
        mfetch0
        sta vm_d
        m_vm_w0
        ldx vm_d
        lda var_lo,x
        and vm_s1
        sta var_lo,x
        lda var_hi,x
        and vm_s2
        sta var_hi,x
        jmp vm_fetch
.endif

op_or                                ; 0x15 : var[v] |= w()
.if 1
        mfetch0x sfw10
sfr10
.else
        mfetch0
.endif
.if 1
        tax                         ; X = v (survives mfetch)
        mfetch0                     ; high byte first: OR straight into the variable
        ora var_hi,x
        sta var_hi,x
        mfetch0
        ora var_lo,x
        sta var_lo,x
.else
        sta vm_d
        m_vm_w0
        ldx vm_d
        lda var_lo,x
        ora vm_s1
        sta var_lo,x
        lda var_hi,x
        ora vm_s2
        sta var_hi,x
.endif
        jmp vm_fetch
.if 1
sfw10   jsr pl_wrap
        jmp sfr10
.endif

.if 1
        nocross op_shl, sh_b, sh_d   ; the zero-count hop
.endif
op_shl                               ; 0x16 : var[v] <<= (w() & 15)
.if 1
        mfetch0x sfw5
sfr5
.else
        mfetch0
.endif
.if 1
        tax                         ; X = v
        inc pl_wlo                  ; the count word's high byte is never read:
        bne ?lk                      ;   step over it (the pointer walk mfetch does)
        jsr pl_wrap
?lk      mfetch0                     ; A = low byte
        and #15
        tay
        beq sh_d                    ; (count 0: nothing to shift)
sh_b
        lda var_hi,x                ; the word shifts in A + one zp cell (asl @ 2 +
        sta vm_s2                   ;   rol zp 5 a step) and is stored once, instead
        lda var_lo,x                ;   of asl/rol on var,x (7 + 7)
        cpy #8
        bcc ?lls                    ; count 1-7: the shift loop
        sta vm_s2                   ; count 8-15: <<8 == lo -> hi, lo = 0; then
        tya                         ;   shift the remainder (C = 1 from the cpy:
        sbc #8                      ;   the -8 is exact)
        tay
        lda #0
        cpy #0                      ; (the lda cleared the tay's Z)
        bne ?lls
        beq ?lst                    ; (Z = 1: always)
?lls     asl @
        rol vm_s2
        dey
        bne ?lls
?lst    sta var_lo,x
        lda vm_s2
        sta var_hi,x
sh_d    jmp vm_fetch
.else
        sta vm_d
        m_vm_w0
        lda vm_s1
        and #15
        tay
        beq ?done
        ldx vm_d
?lp     asl var_lo,x
        rol var_hi,x
        dey
        bne ?lp
.endif
?done   jmp vm_fetch
.if 1
sfw5    jsr pl_wrap
        jmp sfr5
.endif

.if 1
        nocross op_shr, shr_b, shr_d   ; the zero-count hop
.endif
op_shr                               ; 0x17 : var[v] = (var[v]&0xFFFF) >> (w() & 15)
.if 1
        mfetch0x sfw4
sfr4
.else
        mfetch0
.endif
.if 1
        tax                         ; X = v
        inc pl_wlo                  ; the count word's high byte is never read:
        bne ?rk                      ;   step over it (the pointer walk mfetch does)
        jsr pl_wrap
?rk      mfetch0                     ; A = low byte
        and #15
        tay
        beq shr_d                   ; (count 0: nothing to shift)
shr_b
        lda var_lo,x                ; the word shifts in A + one zp cell, stored once
        sta vm_s2
        lda var_hi,x
        cpy #8
        bcc ?rls                    ; count 1-7: the shift loop
        sta vm_s2                   ; count 8-15: >>8 == hi -> lo, hi = 0; then
        tya                         ;   shift the remainder (C = 1 from the cpy:
        sbc #8                      ;   the -8 is exact)
        tay
        lda #0
        cpy #0                      ; (the lda cleared the tay's Z)
        bne ?rls
        beq ?rst                    ; (Z = 1: always)
?rls     lsr @
        ror vm_s2
        dey
        bne ?rls
?rst    sta var_hi,x
        lda vm_s2
        sta var_lo,x
shr_d   jmp vm_fetch
.else
        sta vm_d
        m_vm_w0
        lda vm_s1
        and #15
        tay
        beq ?done
        ldx vm_d
?lp     lsr var_hi,x
        ror var_lo,x
        dey
        bne ?lp
.endif
?done   jmp vm_fetch
.if 1
sfw4    jsr pl_wrap
        jmp sfr4
.endif

op_sound                             ; 0x18 : DRAWSOUND -> POKEY sample player
.if 1
        inc pl_wlo                   ; resId high byte: not needed (sound resIds < 256)
        bne ?s0
        jsr pl_wrap
?s0     mfetch0
        sta vm_s1                    ; resId lo
        mfetch0
        sta snd_req_freq             ; freq -> AUDF1
        lda pl_wlo                   ; vol + channel (ignored: 1 voice, volume-only):
        clc                          ;   skipped unread
        adc #2
        sta pl_wlo
        bcc ?s1
        jsr pl_wrap
?s1
.else
        m_vm_w0                     ; resId : vm_s1 = lo (sound resIds < 256)
        mfetch0
        sta snd_req_freq             ; freq -> AUDF1
        mfetch0                       ; vol (ignored: 1-voice volume-only)
        mfetch0                       ; channel (ignored)
.endif
        ldx cur_dir_start            ; search this part's directory slice: an entry
        ldy cur_dir_cnt              ;   matches on resId AND (freq exact | $FF wild).
        beq ?scont                   ;   Capped (resampled) variants precede the
?sscan  lda snd_dir_resid,x          ;   wildcard, so first-match prefers them.
        cmp vm_s1
        bne ?snext
        lda snd_dir_freq,x          ; skill-ok TABX: 223 entries (cannot fit a page),
                                    ;   read a few times a frame
        cmp #$FF
        beq ?sfound                  ; native wildcard (any freq)
        cmp snd_req_freq
        beq ?sfound                  ; exact rate-capped variant for this freq
?snext  inx
        dey
        bne ?sscan
        jmp vm_fetch                  ; resId not in this part's set -> ignore
?sfound cmp #$FF                     ; A = the matched entry's freq tag
        bne ?scap
        ldy snd_req_freq             ; wildcard: AUDF1 = snd_audf[min(freq,39)]
        cpy #40
        bcc ?sw
        ldy #39
?sw     lda snd_audf,y
        jmp ?sfok
?scap   lda #SND_AUDF_CAP            ; capped variant: always plays at the cap rate
?sfok   sta AUDF1
        jsr snd_play                 ; X = directory index
?scont  jmp vm_fetch

; op_memlist moved to src_game/game_vm_memlist.asm (COLD, 0 execs/frame -> out
; of the hot chain, into the $0900 region after game_text; dispatch reaches it).

op_music                             ; 0x1A : (stub) consume w()+w()+b()
.if 1
        lda pl_wlo                  ; skip the 5 operand bytes unread (VRAM reads have
        clc                         ;   no side effect): one add, one wrap check
        adc #5
        sta pl_wlo
        bcc ?mdone
        jsr pl_wrap                 ; the window page was crossed
?mdone  jmp vm_fetch
.else
        m_vm_w0
        m_vm_w0
        mfetch0
        jmp vm_fetch
.endif

