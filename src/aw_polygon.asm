
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
.if 1
;   Skill pass 2026-09-09: Y == 0 is an INVARIANT inside the shape decoder (set at
;   poly_draw entry, restored after every do_hier child); pf_wrap, set_poly_ptr,
;   get_dr_off, mul_zoom/fmul (X only) all keep it, and the sound IRQs never touch Y.
;   Only the decoder calls poly_fetch (the replayer has pl_byte).
.proc poly_fetch
        lda (pb_ptr),y              ; Y = 0 (decoder invariant)
.else
.proc poly_fetch
        ldy #0
        lda (pb_ptr),y
.endif
        inc pb_ptr
        beq pf_wrap                 ; ~1/256 : window page crossed
        rts
.endp

; m_pfetch : poly_fetch INLINE (the decoder reads the stream byte by byte, and the
;   jsr/rts is 12 of its ~20 cycles). Y = 0 on entry and on exit.
.macro m_pfetch
        lda (pb_ptr),y              ; Y = 0 (decoder invariant)
        inc pb_ptr
        bne *+5                     ; no wrap (common) -> skip the 3-byte jsr
        jsr pf_wrap
.endm

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
.if 1
.proc mul_zoom
        lda dr_zoom+1
        bne ?slow
        lda dr_zoom
        cmp #64
        bne ?slow
        lda mul_m                   ; scaled = m  (bit-identical to (m*64)>>6)
        sta scaled_lo
        sty scaled_hi               ; = 0 (decoder Y invariant) -- A = scaled_lo
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
        lda prod1                   ; >>6 = the bytes 1-2 of the product << 2: two
        asl prod0                   ;   24-bit shifts LEFT with the middle byte in A
        rol @                       ;   (6502 skill MPAIR: shift in A, not 6x lsr /
        rol prod2                   ;   ror / ror in memory)
        asl prod0
        rol @
        rol prod2
        ldx prod2
        stx scaled_hi
        ldx #0                      ; (X = 0 on exit, as the old dex loop left it)
        sta scaled_lo               ; last: A = scaled_lo (read_scaled contract)
        rts
.endp
.else
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
.endif

; read_scaled : scaled = next poly byte * dr_zoom // 64.  The jmp operand is
;   PATCHED per shape by op_drawpoly (dr_zoom is constant through the whole
;   poly_draw tree -- do_hier children inherit it): zoom==64 (1:1, ~97% of all
;   intro shapes) -> rs_fast, which skips mul_zoom entirely ((m*64)>>6 == m);
;   anything else -> rs_slow (the full multiply path).
.if 1
        nocross read_scaled, rs_fast, rs_z4   ; the three entries share one page (do_fill
                                    ;   tells them apart by the operand's lo byte)
; read_scaled CONTRACT (skill pass 3, 2026-09-09): every path returns with
;   A = scaled_lo (the value is still in the accumulator, so the callers add /
;   subtract it directly instead of reloading the cell -- the `sta Y / lda Y` rule
;   across a jsr). scaled_hi is still stored (it is the carry-in for the hi byte).
read_scaled
rs_smc  jmp rs_fast                 ; operand = rs_fast / rs_slow (SMC, per shape)

.proc rs_fast                       ; zoom == 64 : scaled = byte, exactly
        lda (pb_ptr),y              ; inlined poly_fetch ; Y = 0 (decoder invariant)
        inc pb_ptr
        beq ?w
        sta scaled_lo
        sty scaled_hi               ; = 0 (Y invariant) -- A keeps scaled_lo
        rts
?w      jsr pf_wrap                 ; rare window cross (preserves A)
        sta scaled_lo
        sty scaled_hi
.else
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
.endif
        rts
.endp

.proc rs_slow                       ; zoom >= 16384 (never in practice): full (m*zoom)>>6
        stx ?xs                     ; X is kept: do_fill's vertex loop holds its index
        jsr poly_fetch              ;   there across the reads (mul_zoom's fmul_b uses X)
        sta mul_m
        jsr mul_zoom                ; A = scaled_lo (read_scaled contract)
        ldx ?xs
        rts
?xs     dta 0
.endp

.if 1
; rs_z4 : the per-SHAPE premultiply (the game fork's pass). rs_z4_set patched
;   z4 = zoom<<2 into the square-table operands below, so per coordinate this is
;   just TWO table multiplies:  (m*zoom)>>6 == (m*z4)>>8 == m*z4_hi + hi(m*z4_lo)
;   (an exact integer identity for zoom < 16384). ~85 cyc/coord instead of the
;   fmul_seta + 2x fmul_b + >>6 shift chain mul_zoom runs (~200).
.proc rs_z4
        lda (pb_ptr),y              ; inlined poly_fetch ; Y = 0 (decoder invariant)
        inc pb_ptr
        beq ?w
?go     tay                         ; Y = m (the table 'b' index; X stays free)
        sec
fzh_1   lda fmul_sq1l,y             ; p2 = z4_hi * m
fzh_2   sbc fmul_sq2l,y
        sta scaled_lo
fzh_3   lda fmul_sq1h,y
fzh_4   sbc fmul_sq2h,y
        sta scaled_hi
        sec                         ; p1 = z4_lo * m ; only hi(p1) is added, but
fzl_1   lda fmul_sq1l,y             ;   the hi sbc needs the lo borrow -> chain both
fzl_2   sbc fmul_sq2l,y
fzl_3   lda fmul_sq1h,y
fzl_4   sbc fmul_sq2h,y
        clc                         ; scaled = p2 + hi(p1)  (<= 65280, no overflow)
        adc scaled_lo
        sta scaled_lo
        bcc ?done
        inc scaled_hi
?done   ldy #0                      ; restore the decoder's Y = 0 invariant
        rts
?w      jsr pf_wrap                 ; rare window cross (preserves A)
        jmp ?go
.endp

; rs_z4_set : z4 = dr_zoom << 2 -> the eight square-table operands above. Once per
;   zoomed shape (the replayer's per-shape dispatch).
.proc rs_z4_set
        lda dr_zoom+1
        sta tmp_hi
        lda dr_zoom
        asl @
        rol tmp_hi
        asl @
        rol tmp_hi                  ; A = z4 lo ; tmp_hi = z4 hi
        sta rs_z4.fzl_1+1
        sta rs_z4.fzl_3+1
        eor #$FF
        sta rs_z4.fzl_2+1
        sta rs_z4.fzl_4+1
        lda tmp_hi
        sta rs_z4.fzh_1+1
        sta rs_z4.fzh_3+1
        eor #$FF
        sta rs_z4.fzh_2+1
        sta rs_z4.fzh_4+1
        rts
.endp
.endif


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
.if 1
;   in : dv_lo:dv_hi (signed dx), hh (8-bit, dy>=1)
;        X = 0 (right edge) or SMC_LD (left edge)   (skill pass 2026-09-09, ported
;        from the game fork)
;   out: the 4 step bytes are written DIRECTLY into fill_poly_int's ?row SMC
;        adc-operands via abs,x -- the old N0..N3 staging + the caller's
;        8-instruction copy block per edge are gone. The negate is folded into the
;        write-out (0 - N, borrow-chained = the old eor/adc two's complement pass).
;        Same values, same wrap. |dx| < 256 for every intro edge (measured), so only
;        the low byte is negated (the old code negated dv_hi too but never read it).
; (calc_step is inline in fill_poly_int now, src/aw_raster.asm: one copy per edge on
;  absolute SMC operands -- the rare dy==1 / dx<0 halves in src/aw_raster_rare.asm)
.else
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
.endif

;=============================================================================
; Polygon decoder  (port of PolyData.draw / _fill / _hier)
;=============================================================================

; poly_draw : draw the shape at dr_off with dr_x,dr_y,dr_zoom,dr_col.
.if 1
.proc poly_draw
        ldy #0                      ; the decoder's Y = 0 invariant (see poly_fetch)
        jsr poly_fetch              ; A = byte0 ; dr_off++
        cmp #$C0
        bcc ?notfill
        ; filled polygon : col = (dr_col&0x80) ? (byte0&0x3F) : dr_col
        ; (skill pass 2026-09-09: `bit` tests dr_col's bit 7 without touching A, so
        ;  byte0 stays in A -- the pha/lda/and/pla shuffle is gone. Same test, same col.)
        bit dr_col
        bpl ?usecol
        and #$3F
        sta fill_col
        jmp do_fill
?usecol lda dr_col
        sta fill_col
        jmp do_fill
?notfill
        and #$3F
        cmp #2
        bne ?ret
        jmp do_hier
?ret    rts
.endp
.else
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
.endif

; do_fill : read bbox + vertices, build the point list, rasterise.
.if 1
        nocross2 do_fill, do_fill.df_vy, do_fill.df_vye, do_fill.df_disp, do_fill.df_dse
.proc do_fill
        lda rs_smc+1                ; ONE zoom test per fill: zoom 64 (rs_fast, ~97% of
        cmp #<rs_slow               ;   the intro) reads the stream inline below; rs_z4 /
        jcs ?gen                    ;   rs_slow out of line (lo bytes fast < slow < z4)
        m_pfetch                    ; bbw = byte, hi 0 (Y = 0, the decoder invariant)
        sta bbw
        sty bbw+1
        lsr @                       ; x0 = dr_x - bbw/2 (half hi = 0):
        eor #$FF                    ;   dr_x + ~half + 1, then dr_x+1 + $FF + C
        sec
        adc dr_x
        sta x0
        lda dr_x+1
        adc #$FF
        sta x0+1
        m_pfetch                    ; bbh
        sta bbh
        sty bbh+1
        lsr @                       ; y0 = dr_y - bbh/2
        eor #$FF
        sec
        adc dr_y
        sta y0
        lda dr_y+1
        adc #$FF
        sta y0+1
        m_pfetch                    ; n verts
        sta nverts
        ldx #0                      ; X = vertex index
        asl @                       ; the 2n vertex bytes are read (pb),y and the pointer
        bcs ?vlf                    ;   moves once after -- unless they may run past the
        adc pb_ptr                  ;   window end ($7FFF), or n >= 128 (Y would wrap).
        bcc df_vy                   ;   C = 0 here.
        lda pb_ptr+1
        cmp #$7F
        beq ?vlf                    ; last window page + a page cross: byte by byte
df_vy   lda (pb_ptr),y              ; px = x0 + byte
        iny
        clc
        adc x0
        sta pts_xlo,x
        lda x0+1
        adc #0
        sta pts_xhi,x
        lda (pb_ptr),y              ; py = y0 + byte
        iny
        clc
        adc y0
        sta pts_ylo,x
        lda y0+1
        adc #0
        sta pts_yhi,x
        inx
        cpx nverts
        bne df_vy
df_vye  tya                         ; pb_ptr += 2n, the window wrap as the byte
        clc                         ;   fetch does it (pf_wrap on a low-byte wrap)
        adc pb_ptr
        sta pb_ptr
        ldy #0                      ; the decoder's Y = 0 invariant
        bcc df_disp
        jsr pf_wrap                 ; (rare window cross)
        jmp df_disp
?vlf    lda (pb_ptr),y              ; px = x0 + byte
        inc pb_ptr
        bne ?f1
        jsr pf_wrap                 ; (rare window cross, keeps A)
?f1     clc
        adc x0
        sta pts_xlo,x
        lda x0+1
        adc #0
        sta pts_xhi,x
        lda (pb_ptr),y              ; py = y0 + byte
        inc pb_ptr
        bne ?f2
        jsr pf_wrap
?f2     clc
        adc y0
        sta pts_ylo,x
        lda y0+1
        adc #0
        sta pts_yhi,x
        inx
        cpx nverts
        bne ?vlf
        ; --- per-shape clip dispatch (SMC) : bbox fully on-screen (75% of intro
        ; fills; vertices never leave the bbox -- measured over the whole intro) ->
        ; the raster's three draw sites go to draw_scanline_fast (no per-row y test /
        ; X clip); anything else -> the full clip path. The sites keep the last
        ; target (usually the same): unchanged -> no stores. X holds x1/y1 lo, so the
        ; decoder's Y = 0 survives.
df_disp lda x0+1
        ora y0+1
        bmi df_clip                 ; x0 < 0 or y0 < 0
        lda x0                      ; x1 = x0 + bbw  must be <= 319
        clc
        adc bbw
        tax
        lda x0+1
        adc bbw+1
        beq ?xok                    ; x1 <= 255 -> ok
        cmp #1
        bne df_clip                 ; x1 >= 512
        cpx #$40
        bcs df_clip                 ; x1 >= 320
?xok    lda y0                      ; y1 = y0 + bbh  must be <= 199
        clc
        adc bbh
        tax
        lda y0+1
        adc bbh+1
        bne df_clip
        cpx #SCRH
        bcs df_clip                 ; y1 >= 200
        lda #<draw_scanline_fast
        cmp fill_poly_int.smc_dsl+1
        beq df_dse                  ; unchanged -> no stores
        ldx #>draw_scanline_fast
        bne ?set                    ; (X != 0: always)
df_clip lda #<draw_scanline
        cmp fill_poly_int.smc_dsl+1
        beq df_dse
        ldx #>draw_scanline
?set    sta fill_poly_int.smc_dsl+1
        stx fill_poly_int.smc_dsl+2
        sta fill_poly_int.smc_dsh+1 ; the paired loop has two more dispatched draw
        stx fill_poly_int.smc_dsh+2 ;   sites (pair row + odd-exit row)
        sta fill_poly_int.smc_dsi+1
        stx fill_poly_int.smc_dsi+2
df_dse  jmp fill_poly_int
        ert <draw_scanline_fast=<draw_scanline   ; the low byte alone tells them apart
?gen    bne ?gz                     ; C = 1 ; Z = rs_slow (zoom >= 16384, never in
        lda #<rs_slow               ;   practice): run the rs_z4 copy below with its four
        jsr ?gpat                   ;   reads pointed at rs_slow, then point them back
        jsr ?gz                     ;   (?gz ends in jmp fill_poly_int -> its rts lands here)
        lda #<rs_z4
?gpat   sta ?gz+1
        sta ?gzh+1
        sta ?gzv+1
        sta ?gzy+1
        rts
?gz     jsr rs_z4                    ; bbw  (A = scaled_lo, the read_scaled contract)
        sta bbw
        lda scaled_hi
        sta bbw+1
?gzh    jsr rs_z4                    ; bbh
        sta bbh
        lda scaled_hi
        sta bbh+1
        m_pfetch                    ; n verts
        sta nverts
        lda bbw+1                   ; x0 = dr_x - bbw/2  (dr_x + ~half + 1)
        lsr @
        tax                         ; X = half hi ; C = bit 0 -> the lo ror
        lda bbw
        ror @
        eor #$FF
        sec
        adc dr_x
        sta x0
        txa
        eor #$FF
        adc dr_x+1
        sta x0+1
        lda bbh+1                   ; y0 = dr_y - bbh/2
        lsr @
        tax
        lda bbh
        ror @
        eor #$FF
        sec
        adc dr_y
        sta y0
        txa
        eor #$FF
        adc dr_y+1
        sta y0+1
        ldx #0                      ; X = vertex index, kept live across the reads
?gzv    jsr rs_z4                    ; px = x0 + scaled
        clc
        adc x0
        sta pts_xlo,x
        lda x0+1
        adc scaled_hi
        sta pts_xhi,x
?gzy    jsr rs_z4                    ; py = y0 + scaled
        clc
        adc y0
        sta pts_ylo,x
        lda y0+1
        adc scaled_hi
        sta pts_yhi,x
        inx
        cpx nverts
        bne ?gzv
        jmp df_disp
        ert <rs_fast>=<rs_slow      ; the lo-byte order the entry test relies on
        ert <rs_slow>=<rs_z4
        ert [>rs_fast]<>[>rs_z4]    ; one page: the lo byte alone tells them apart
        ert [>rs_slow]<>[>rs_z4]    ;   (and ?gpat patches the lo bytes only)
.endp
.else
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
.endif

; do_hier : group node ; recurse over children.
.if 1
        nocross2 do_hier, do_hier.dh_wh, do_hier.dh_whe, do_hier.dh_sel, do_hier.dh_cd
.proc do_hier
        lda rs_smc+1                ; the zoom is the same for the whole tree: test it
        cmp #<rs_fast               ;   ONCE here -- zoom 64 reads bx/by inline and sets
        jeq ?hf                     ;   dh_sel = bit (the child reads inline); any other
        jsr read_scaled             ;   zoom: read_scaled, dh_sel = jmp dh_cgen
        eor #$FF                    ; bx = dr_x - scaled  (A = scaled_lo:
        sec                         ;   dr_x + ~scaled + 1, carry-identical to sbc)
        adc dr_x
        sta bx
        lda dr_x+1
        sbc scaled_hi
        sta bx+1
        jsr read_scaled             ; by = dr_y - scaled
        eor #$FF
        sec
        adc dr_y
        sta by
        lda dr_y+1
        sbc scaled_hi
        sta by+1
        ldx #$4C                    ; jmp
?hsel   stx dh_sel
        m_pfetch                    ; child count (inlined fetch)
        sta hcount                  ; loop hcount+1 times
?loop
dh_wh
        m_pfetch                    ; word hi (big-endian)
dh_whe  sta word_hi
        m_pfetch                    ; word lo
        sta word_lo
        ; the child position is composed STRAIGHT into dr_x/dr_y (nothing between
        ; here and the child draw reads them; bx/by were derived already).
dh_sel  bit dh_cgen                 ; SMC opcode (set above): bit = zoom 64, the reads
                                    ;   below inline; jmp = the generic path (out of line)
        lda (pb_ptr),y              ; dr_x = cx = bx + byte  (Y = 0)
        inc pb_ptr
        bne ?c1
        jsr pf_wrap
?c1     clc
        adc bx
        sta dr_x
        lda bx+1
        adc #0
        sta dr_x+1
        lda (pb_ptr),y              ; dr_y = cy = by + byte
        inc pb_ptr
        bne ?c2
        jsr pf_wrap
?c2     clc
        adc by
        sta dr_y
        lda by+1
        adc #0
        sta dr_y+1
dh_cd
?cdone  lda #$FF
        sta ccol
        lda word_hi
        bpl ?nocol                  ; bit15 clear -> no per-child colour
        m_pfetch                    ; ccol = poly[dr_off] & 0x7F
        and #$7F
        sta ccol
        m_pfetch                    ; (python off += 2 : skip the 2nd byte)
?nocol
        ; --- save _hier state, recurse, restore --- the PARENT stream pointer
        ; (poly_bnk:pb_ptr) is cached on pstk instead of get_dr_off here + set_poly_ptr
        ; on restore (the game fork's pass): poly_base_adj-free intro banks, so the
        ; saved pointer IS what the recompute would reproduce. 9-byte frame at pstk+psp.
        ldx psp
        lda poly_bnk
        sta pstk,x
        lda pb_ptr
        sta pstk+1,x
        lda pb_ptr+1
        sta pstk+2,x
        lda bx
        sta pstk+3,x
        lda bx+1
        sta pstk+4,x
        lda by
        sta pstk+5,x
        lda by+1
        sta pstk+6,x
        lda hcount
        sta pstk+7,x
        lda dr_col
        sta pstk+8,x
        txa
        clc
        adc #9
        sta psp
        ; child draw params : dr_off = (word & 0x7FFF) * 2, set_poly_ptr inline
        lda word_lo
        asl @
        sta dr_off
        sta pb_ptr                  ; (the window low byte)
        lda word_hi
        and #$7F
        rol @
        sta dr_off+1
        tax
        lda poly_bank_lut,x         ; ((hi>>6)+POLY_BANK0)|$80
        sta poly_bnk
        sta memb_cur
        sta VBXE_MEMAC_B
        lda poly_win_lut,x          ; (hi&$3F)|>DATAW
        sta pb_ptr+1
        lda ccol                    ; (dr_x/dr_y already hold cx/cy, see above)
        sta dr_col
        jsr poly_draw
        ldy #0                      ; the child's raster clobbered Y -> restore the
        lda psp                     ;   decoder's Y = 0 invariant for the fetches below
        sec
        sbc #9
        sta psp
        tax
        lda pstk+8,x
        sta dr_col
        lda pstk+7,x
        sta hcount
        lda pstk+6,x
        sta by+1
        lda pstk+5,x
        sta by
        lda pstk+4,x
        sta bx+1
        lda pstk+3,x
        sta bx
        lda pstk+2,x
        sta pb_ptr+1
        lda pstk+1,x
        sta pb_ptr
        lda pstk,x
        sta poly_bnk                ; parent stream pointer restored directly
        cmp memb_cur                ; re-own MEMAC-B only when the child left another
        beq ?samebk                 ;   bank (memb_cur leads: the sound IRQ restores
        sta memb_cur                ;   the register to it)
        sta VBXE_MEMAC_B
?samebk dec hcount
        bmi ?hdone                  ; childcount+1 iterations
        jmp ?loop
?hdone  lda psp                     ; the outermost group leaves dr_off = its last
        beq ?dro                    ;   save point, as the get_dr_off walk did
        rts
?dro    jmp get_dr_off
?hf     m_pfetch                    ; zoom 64: bx = dr_x - byte (hi 0)
        eor #$FF
        sec
        adc dr_x
        sta bx
        lda dr_x+1
        sbc #0
        sta bx+1
        m_pfetch                    ; by = dr_y - byte
        eor #$FF
        sec
        adc dr_y
        sta by
        lda dr_y+1
        sbc #0
        sta by+1
        ldx #$2C                    ; bit
        jmp ?hsel
dh_cgen                             ; zoom != 64: the generic child read, out of line
        jsr read_scaled             ; dr_x = cx = bx + scaled  (A = scaled_lo)
        clc
        adc bx
        sta dr_x
        lda bx+1
        adc scaled_hi
        sta dr_x+1
        jsr read_scaled             ; dr_y = cy = by + scaled
        clc
        adc by
        sta dr_y
        lda by+1
        adc scaled_hi
        sta dr_y+1
        jmp ?cdone
.endp
.else
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
.endif
