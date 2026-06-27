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

op_remove                            ; 0x11 : kill this thread
        ldx vm_t
        lda #$FF
        sta tpc_hi,x
        lda #1
        sta vm_goto
        sta vm_rem
        jmp vm_cont

op_drawstring                        ; 0x12 : DRAWTEXT -> game_text.asm (intro glyph blitter).
        jmp do_drawstring            ;   global label keeps op_remove/op_sub ?-scopes separate.

op_sub                               ; 0x13 : var[d] -= var[s]
        mfetch
        sta vm_d
        mfetch
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
        mfetch
        sta vm_d
        m_vm_w
        ldx vm_d
        lda var_lo,x
        and vm_s1
        sta var_lo,x
        lda var_hi,x
        and vm_s2
        sta var_hi,x
        jmp vm_fetch

op_or                                ; 0x15 : var[v] |= w()
        mfetch
        sta vm_d
        m_vm_w
        ldx vm_d
        lda var_lo,x
        ora vm_s1
        sta var_lo,x
        lda var_hi,x
        ora vm_s2
        sta var_hi,x
        jmp vm_fetch

op_shl                               ; 0x16 : var[v] <<= (w() & 15)
        mfetch
        sta vm_d
        m_vm_w
        lda vm_s1
        and #15
        tay
        beq ?done
        ldx vm_d
?lp     asl var_lo,x
        rol var_hi,x
        dey
        bne ?lp
?done   jmp vm_fetch

op_shr                               ; 0x17 : var[v] = (var[v]&0xFFFF) >> (w() & 15)
        mfetch
        sta vm_d
        m_vm_w
        lda vm_s1
        and #15
        tay
        beq ?done
        ldx vm_d
?lp     lsr var_hi,x
        ror var_lo,x
        dey
        bne ?lp
?done   jmp vm_fetch

op_sound                             ; 0x18 : DRAWSOUND -> POKEY sample player
        m_vm_w                     ; resId : vm_s1 = lo (sound resIds < 256)
        mfetch
        sta snd_req_freq             ; freq -> AUDF1
        mfetch                       ; vol (ignored: 1-voice volume-only)
        mfetch                       ; channel (ignored)
        ldx cur_dir_start            ; search this part's directory slice: an entry
        ldy cur_dir_cnt              ;   matches on resId AND (freq exact | $FF wild).
        beq ?scont                   ;   Capped (resampled) variants precede the
?sscan  lda snd_dir_resid,x          ;   wildcard, so first-match prefers them.
        cmp vm_s1
        bne ?snext
        lda snd_dir_freq,x
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

op_memlist                           ; 0x19 : resource load / part switch
        m_vm_w                    ; num -> vm_s2:vm_s1
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
        sta vm_goto                 ; end this thread slice
        jmp vm_cont
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

op_music                             ; 0x1A : (stub) consume w()+w()+b()
        m_vm_w
        m_vm_w
        mfetch
        jmp vm_fetch

