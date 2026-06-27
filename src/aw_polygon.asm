;=============================================================================
; Playlist fetch : sequential read from VRAM via MEMAC-B.
;   The intro playlist is STRICTLY LINEAR (no jumps), so keep a running window
;   pointer (pl_wlo/pl_whi) + bank (pl_bnk) instead of recomputing bank/window
;   from a 24-bit address every byte. Only the bank CHECK stays per byte
;   (poly_fetch interleaves and steals the MEMAC-B register between ops).
;=============================================================================
.proc pl_byte
        lda pl_bnk
        cmp memb_cur                ; (#2) only switch the bank if poly stole it
        beq ?nosw
        sta memb_cur
        sta VBXE_MEMAC_B
?nosw   ldy #0
        lda (pl_wlo),y              ; A = the playlist byte (return value)
        inc pl_wlo
        beq ?wrap
        rts
?wrap   pha                         ; window page crossed (~1/256 reads)
        inc pl_whi
        lda pl_whi
        cmp #$80                    ; past $7FFF (16 KB window end)?
        bne ?nb
        lda #>DATAW                 ; window back to $4000, next bank
        sta pl_whi
        inc pl_bnk
        lda pl_bnk
        sta memb_cur
        sta VBXE_MEMAC_B
?nb     pla
        rts
.endp

;=============================================================================
; Poly data fetch  (from VRAM via MEMAC-B)
;=============================================================================

; set_poly_ptr : sync the running MEMAC-B stream pointer (poly_bnk + pb_ptr) from
;   dr_off.  Poly data is read SEQUENTIALLY, so the bank/window are computed only
;   here -- on a dr_off JUMP (op_drawpoly start, do_hier child entry + restore) --
;   not per byte.  The MEMAC-B bank is set UNCONDITIONALLY here (memb_cur first:
;   the sound IRQ restores the register to memb_cur, so memb_cur must lead), which
;   lets poly_fetch drop its per-byte bank check: between set_poly_ptr calls only
;   poly_fetch touches the stream, and pl_byte never runs inside a DRAWPOLY.
;     bank = POLY_BANK0 + (off>>14) | $80 ; window = $4000 + (off & $3FFF).
.proc set_poly_ptr
        ldx dr_off+1
        lda poly_bank_lut,x         ; ((hi>>6)+POLY_BANK0)|$80
        sta poly_bnk
        sta memb_cur
        sta VBXE_MEMAC_B
        lda poly_win_lut,x          ; (hi&$3F)|>DATAW
        sta pb_ptr+1
        lda dr_off
        sta pb_ptr
        rts
.endp

; poly_fetch : A = next poly byte ; advances the running pb_ptr only.  No bank
;   check (set_poly_ptr guarantees the bank) and no dr_off upkeep (do_hier derives
;   it at save time via get_dr_off) -> the hot path is 20 cyc + jsr/rts.
.proc poly_fetch
        ldy #0
        lda (pb_ptr),y
        inc pb_ptr
        beq pf_wrap                 ; ~1/256 : window page crossed
        rts
.endp

; pf_wrap : pb_ptr low wrapped (page cross). A = the just-read data byte, so the
;   window check preserves it with pha/pla -- paid only ~1/256 reads. Shared by
;   poly_fetch (fallthrough target) and rs_fast (jsr).
.proc pf_wrap
        pha
        inc pb_ptr+1
        lda pb_ptr+1
        cmp #$80                    ; crossed past $7FFF (16 KB window end)?
        bne ?nc
        lda #>DATAW                 ; reset window to $4000, advance to next bank
        sta pb_ptr+1
        inc poly_bnk
        lda poly_bnk
        sta memb_cur
        sta VBXE_MEMAC_B
?nc     pla
        rts
.endp

; get_dr_off : derive dr_off (the 16-bit poly offset) back from the stream
;   pointer:  dr_off = (poly_bnk - ($80|POLY_BANK0))<<14 | (pb_ptr - DATAW).
;   Called only at the do_hier recursion save point (instead of poly_fetch
;   paying a 16-bit inc on every byte).
.proc get_dr_off
        lda poly_bnk
        sec
        sbc #$80+POLY_BANK0         ; bank delta 0..3 (poly data < 64 KB)
        tax
        lda pb_ptr+1
        sec
        sbc #>DATAW                 ; window hi -> offset hi bits 8..13
        ora pf_bank_hi,x            ; | delta<<6
        sta dr_off+1
        lda pb_ptr
        sta dr_off
        rts
.endp
pf_bank_hi dta $00,$40,$80,$C0

;=============================================================================
; zoom scale :  scaled = (mul_m * dr_zoom) >> 6
;   FAST PATH: zoom == 64 (1:1, ~97% of all calls in the intro) -> (m*64)>>6 == m
;   exactly for m in 0..255, so skip the whole 8x16 multiply + >>6 shift.
;=============================================================================
.proc mul_zoom
        lda dr_zoom+1
        bne ?slow
        lda dr_zoom
        cmp #64
        bne ?slow
        lda mul_m                   ; scaled = m  (bit-identical to (m*64)>>6)
        sta scaled_lo
        lda #0
        sta scaled_hi
        rts
?slow   ; prod(24b) = mul_m * dr_zoom via two fmulu 8x8 (m set once), then >>6
        lda mul_m
        jsr fmul_seta               ; a = mul_m
        ldx dr_zoom                 ; b = zoom_lo
        jsr fmul_b
        lda qp_lo
        sta prod0
        lda qp_hi
        sta prod1
        ldx dr_zoom+1               ; b = zoom_hi
        jsr fmul_b
        lda prod1
        clc
        adc qp_lo
        sta prod1
        lda qp_hi
        adc #0
        sta prod2
        ldx #6
?sh     lsr prod2
        ror prod1
        ror prod0
        dex
        bne ?sh
        lda prod0
        sta scaled_lo
        lda prod1
        sta scaled_hi
        rts
.endp

; read_scaled : scaled = next poly byte * dr_zoom // 64.  The jmp operand is
;   PATCHED per shape by op_drawpoly (dr_zoom is constant through the whole
;   poly_draw tree -- do_hier children inherit it): zoom==64 (1:1, ~97% of all
;   intro shapes) -> rs_fast, which skips mul_zoom entirely ((m*64)>>6 == m);
;   anything else -> rs_slow (the full multiply path).
read_scaled
rs_smc  jmp rs_fast                 ; operand = rs_fast / rs_slow (SMC, per shape)

.proc rs_fast                       ; zoom == 64 : scaled = byte, exactly
        ldy #0
        lda (pb_ptr),y              ; inlined poly_fetch (saves the jsr/rts)
        inc pb_ptr
        beq ?w
        sta scaled_lo
        lda #0
        sta scaled_hi
        rts
?w      jsr pf_wrap                 ; rare window cross (preserves A)
        sta scaled_lo
        lda #0
        sta scaled_hi
        rts
.endp

.proc rs_slow                       ; zoom != 64 : full (m*zoom)>>6
        jsr poly_fetch
        sta mul_m
        jmp mul_zoom                ; tail-call (mul_zoom keeps its own ==64 test)
.endp

;=============================================================================
; fmulu : unsigned 8x8 -> 16 square-table multiply (Fox/Tqa).  qp = a * b.
;   a*b = sq1[a+b] - sq2[(a^FF)+b],  sq1[i]=floor(i*i/4), sq2[j]=floor((255-j)**2/4).
;   The factors index the tables via the SELF-MODIFIED low byte of a page-aligned
;   base (a / a^FF) plus X (=b), so each table is 512 B (a+b reaches 510). ~14 cyc
;   for the lookup; split so the multiplicand 'a' is set once for repeated 'b'.
;   fmul_seta:  A = a   (patches the 4 table operands)
;   fmul_b:     X = b   -> qp_lo:qp_hi = a*b   (call after fmul_seta)   clobbers A
;=============================================================================
fmul_seta
        sta fmlb_l1+1              ; a -> low byte of the sq1 table operands
        sta fmlb_h1+1
        eor #$FF
        sta fmlb_l2+1             ; a^FF -> low byte of the sq2 table operands
        sta fmlb_h2+1
        rts

fmul_b
        sec
fmlb_l1 lda fmul_sq1l,x           ; sq1l[a+b]   (operand low byte patched to a)
fmlb_l2 sbc fmul_sq2l,x          ; sq2l[(a^FF)+b]
        sta qp_lo
fmlb_h1 lda fmul_sq1h,x
fmlb_h2 sbc fmul_sq2h,x
        sta qp_hi
        rts

;=============================================================================
; Edge slope  slope = (|dx| << 16) / dy , sign of dx applied (16.16, in N0..N3).
;   Reciprocal LUT + QS multiply instead of a 32/16 divide: |dx| is < 256 for
;   every edge in this intro (measured), so slope = |dx| * recip[dy], with
;   recip[dy] = round(65536/dy). dy==1 -> |dx|<<16 (recip 65536 won't fit 16b).
;   The 16-bit |dx|*recip is two QS 8x8 multiplies (was an 8x16 shift-add).
;   in : dv_lo:dv_hi (signed dx), hh (8-bit, dy>=1)
;=============================================================================
.proc calc_step
        lda dv_hi
        bpl ?pos
        sec                         ; dv = -dv  (|dx| now in dv_lo, dv_hi=0)
        lda #0
        sbc dv_lo
        sta dv_lo
        lda #0
        sbc dv_hi
        sta dv_hi
        lda #1
        sta dvsign
        jmp ?abs
?pos    lda #0
        sta dvsign
?abs    lda #0
        sta N0
        sta N1
        sta N2
        sta N3
        lda hh
        cmp #1
        bne ?mul
        lda dv_lo                   ; dy==1 : slope = |dx| << 16
        sta N2
        jmp ?sign
?mul    ; slope = |dx| * recip[hh], recip 16-bit -> two fmulu 8x8 multiplies:
        ;   N(24b) = (|dx|*recip_lo) + (|dx|*recip_hi << 8). |dx| is set once.
        lda dv_lo
        jsr fmul_seta               ; a = |dx|  (patched once for both mul-b)
        ldx hh
        lda recip_lo,x
        tax                         ; b = recip_lo
        jsr fmul_b                  ; p0 = |dx| * recip_lo
        lda qp_lo
        sta N0
        lda qp_hi
        sta N1
        ldx hh
        lda recip_hi,x
        tax                         ; b = recip_hi
        jsr fmul_b                  ; p1 = |dx| * recip_hi
        lda N1
        clc
        adc qp_lo
        sta N1
        lda qp_hi
        adc #0
        sta N2                      ; N3 stays 0 (cleared at ?abs)
?sign   lda dvsign
        beq ?done
        lda N0                      ; negate slope (32-bit two's complement)
        eor #$FF
        clc
        adc #1
        sta N0
        lda N1
        eor #$FF
        adc #0
        sta N1
        lda N2
        eor #$FF
        adc #0
        sta N2
        lda N3
        eor #$FF
        adc #0
        sta N3
?done   rts
.endp

;=============================================================================
; Polygon decoder  (port of PolyData.draw / _fill / _hier)
;=============================================================================

; poly_draw : draw the shape at dr_off with dr_x,dr_y,dr_zoom,dr_col.
.proc poly_draw
        jsr poly_fetch              ; A = byte0 ; dr_off++
        cmp #$C0
        bcc ?notfill
        ; filled polygon : col = (dr_col&0x80) ? (byte0&0x3F) : dr_col
        pha
        lda dr_col
        and #$80
        beq ?usecol
        pla
        and #$3F
        jmp ?havecol
?usecol pla
        lda dr_col
?havecol
        sta fill_col
        jmp do_fill
?notfill
        and #$3F
        cmp #2
        bne ?ret
        jmp do_hier
?ret    rts
.endp

; do_fill : read bbox + vertices, build the point list, rasterise.
.proc do_fill
        jsr read_scaled             ; bbw
        lda scaled_lo
        sta bbw
        lda scaled_hi
        sta bbw+1
        jsr read_scaled             ; bbh
        lda scaled_lo
        sta bbh
        lda scaled_hi
        sta bbh+1
        jsr poly_fetch              ; n verts
        sta nverts
        ; x0 = dr_x - bbw/2
        lda bbw+1
        lsr @
        sta tmp_hi
        lda bbw
        ror @
        sta tmp_lo
        lda dr_x
        sec
        sbc tmp_lo
        sta x0
        lda dr_x+1
        sbc tmp_hi
        sta x0+1
        ; y0 = dr_y - bbh/2
        lda bbh+1
        lsr @
        sta tmp_hi
        lda bbh
        ror @
        sta tmp_lo
        lda dr_y
        sec
        sbc tmp_lo
        sta y0
        lda dr_y+1
        sbc tmp_hi
        sta y0+1
        ; --- per-shape clip dispatch (SMC) : bbox fully on-screen (75% of intro
        ; fills, 55% of scanlines; vertices never leave the bbox -- measured over
        ; the whole intro) -> patch the raster's smc_dsl to draw_scanline_fast
        ; (no per-row y test / X clip). Anything else -> the full clip path.
        ldx #<draw_scanline
        ldy #>draw_scanline
        lda x0+1
        bmi ?clip                   ; x0 < 0
        lda y0+1
        bmi ?clip                   ; y0 < 0
        lda x0                      ; x1 = x0 + bbw  must be <= 319
        clc
        adc bbw
        sta tmp_lo
        lda x0+1
        adc bbw+1
        beq ?xok                    ; x1 <= 255 -> ok
        cmp #1
        bne ?clip                   ; x1 >= 512
        lda tmp_lo
        cmp #$40
        bcs ?clip                   ; x1 >= 320
?xok    lda y0                      ; y1 = y0 + bbh  must be <= 199
        clc
        adc bbh
        sta tmp_lo
        lda y0+1
        adc bbh+1
        bne ?clip
        lda tmp_lo
        cmp #SCRH
        bcs ?clip                   ; y1 >= 200
        ldx #<draw_scanline_fast
        ldy #>draw_scanline_fast
?clip   stx fill_poly_int.smc_dsl+1
        sty fill_poly_int.smc_dsl+2
        lda #0
        sta vidx
?vl     jsr read_scaled             ; px = x0 + scaled
        lda x0
        clc
        adc scaled_lo
        pha
        lda x0+1
        adc scaled_hi
        tay
        ldx vidx
        pla
        sta pts_xlo,x
        tya
        sta pts_xhi,x
        jsr read_scaled             ; py = y0 + scaled
        lda y0
        clc
        adc scaled_lo
        pha
        lda y0+1
        adc scaled_hi
        tay
        ldx vidx
        pla
        sta pts_ylo,x
        tya
        sta pts_yhi,x
        inc vidx
        lda vidx
        cmp nverts
        bne ?vl
        jmp fill_poly_int
.endp

; do_hier : group node ; recurse over children.
.proc do_hier
        jsr read_scaled             ; bx = dr_x - scaled
        lda dr_x
        sec
        sbc scaled_lo
        sta bx
        lda dr_x+1
        sbc scaled_hi
        sta bx+1
        jsr read_scaled             ; by = dr_y - scaled
        lda dr_y
        sec
        sbc scaled_lo
        sta by
        lda dr_y+1
        sbc scaled_hi
        sta by+1
        jsr poly_fetch              ; child count
        sta hcount                  ; loop hcount+1 times
?loop
        jsr poly_fetch              ; word hi (big-endian)
        sta word_hi
        jsr poly_fetch              ; word lo
        sta word_lo
        jsr read_scaled             ; cx = bx + scaled
        lda bx
        clc
        adc scaled_lo
        sta cx
        lda bx+1
        adc scaled_hi
        sta cx+1
        jsr read_scaled             ; cy = by + scaled
        lda by
        clc
        adc scaled_lo
        sta cy
        lda by+1
        adc scaled_hi
        sta cy+1
        lda #$FF
        sta ccol
        lda word_hi
        bpl ?nocol                  ; bit15 clear -> no per-child colour
        jsr poly_fetch              ; ccol = poly[dr_off] & 0x7F
        and #$7F
        sta ccol
        jsr poly_fetch              ; (python off += 2 : skip the 2nd byte)
?nocol
        ; --- save _hier state, recurse, restore ---
        jsr get_dr_off              ; poly_fetch no longer tracks dr_off per byte
        ldx psp
        lda dr_off
        sta pstk,x
        inx
        lda dr_off+1
        sta pstk,x
        inx
        lda bx
        sta pstk,x
        inx
        lda bx+1
        sta pstk,x
        inx
        lda by
        sta pstk,x
        inx
        lda by+1
        sta pstk,x
        inx
        lda hcount
        sta pstk,x
        inx
        lda dr_col
        sta pstk,x
        inx
        stx psp
        ; child draw params : dr_off = (word & 0x7FFF) * 2
        lda word_lo
        asl @
        sta dr_off
        lda word_hi
        and #$7F
        rol @
        sta dr_off+1
        lda cx
        sta dr_x
        lda cx+1
        sta dr_x+1
        lda cy
        sta dr_y
        lda cy+1
        sta dr_y+1
        lda ccol
        sta dr_col
        jsr set_poly_ptr            ; dr_off jumped to the child -> sync stream ptr
        jsr poly_draw
        ; restore
        ldx psp
        dex
        lda pstk,x
        sta dr_col
        dex
        lda pstk,x
        sta hcount
        dex
        lda pstk,x
        sta by+1
        dex
        lda pstk,x
        sta by
        dex
        lda pstk,x
        sta bx+1
        dex
        lda pstk,x
        sta bx
        dex
        lda pstk,x
        sta dr_off+1
        dex
        lda pstk,x
        sta dr_off
        stx psp
        jsr set_poly_ptr            ; dr_off restored after the child -> re-sync
        dec hcount
        bmi ?hdone                  ; childcount+1 iterations
        jmp ?loop
?hdone  rts
.endp
