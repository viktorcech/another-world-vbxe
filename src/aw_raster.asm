;=============================================================================
; Integer 16.16 raster (mirror of fill_poly_int / _slope in tools/aw_sim.py)
;=============================================================================
; zero-page cells (defined before use: a forward zp equate assembles as absolute in
; MADS's first pass). dv_lo / dv_hi are free: the inline step code keeps dx in Y.
npair   = dv_lo                         ; the half loop's pairs left
.if 1
.if 1
        nocross2 fill_poly_int, fill_poly_int, fill_poly_int.fpi_poly+1, fill_poly_int.fpi_tail, fill_poly_int.smc_dsi+3
.endif
        nocross draw_scanline_fast, draw_scanline_fast.dsf_a, draw_scanline_fast.dsf_xl
.endif
.proc fill_poly_int
        lda fill_col
        sta poly_color
        sta scol                    ; PERF: scol is invariant for the whole shape -- set it
                                    ;   ONCE here (A still = fill_col) instead of reloading
                                    ;   poly_color->scol in emit_span on every span. (draw_dots
                                    ;   below sets its own scol; text uses emit_run, see there.)
        lda nverts
        cmp #3
.if 1
        bcc ?dots                   ; n < 3 (rare): draw_dots, out of line
.else
        bcs fpi_poly
        jmp draw_dots
.endif
?poly
fpi_poly
.if 1
        ; vbxe-blitter skill ("write only the fields that change", "fire early, wait
        ; late"): the blitter reads a BCB only at START, so this shape's colour mode
        ; goes in HERE, not in a test on every span. The fused spans below then only
        ; write the address + width and START -- dsf_m = bne (solid / transparent) or
        ; beq (copy: the source too).
        ldx #$D0
        lda #0
        sta slen_hi                 ; LR spans are < 256 wide: WIDTH+1 stays 0
        lda scol
        cmp #$11
        bcs ?mcpy                   ; copy mode (out of line)
?ma     stx draw_scanline_fast.dsf_m
        cmp last_scol
        bne ?mset                   ; a new colour mode (out of line, above)
?mok
.endif
.if 1
        ; (skill pass 2026-09-09, same as the game fork: indices composed ONCE -- i=1,
        ;  j=n-2 -- X = n-1 indexes cr straight from nverts. Same values, same reads.)
        lda #1
        sta i_idx                   ; i = 1 (the first segment is pts[0] -> pts[1])
        ldx nverts
        txa
        lsr @
        sta numv                    ; n/2 : one ?seg pass per segment, +1 (n is even:
        dex                         ;   an odd n never reached 0 -- a hang before too)
        ; cr0/cr1/cl0/cl1 are not set here: n >= 3, so the first ?seg always reaches the
        ; per-segment reset below before any read. +$80 mod 256 = eor #$80 (C is dead).
        lda pts_xlo,x               ; cr = (pts_x[j] + $8000) << 16  (X biased to
        sta cr2                     ;   unsigned: +$8000 = +$80 in the integer hi byte,
        lda pts_xhi,x               ;   so the edge compare/clip can be unsigned)
        eor #$80
        sta cr3
        dex
        stx j_idx                   ; j = n-2
        lda pts_ylo                 ; hy = pts_y[0]
        sta hy_lo
        lda pts_yhi
        sta hy_hi
        lda pts_xlo                 ; cl = (pts_x[0] + $8000) << 16
        sta cl2
        lda pts_xhi
        eor #$80
        sta cl3
?seg    dec numv
        bne ?cont
        rts
?dots   jmp draw_dots
?mcpy   ldx #$F0                    ; copy mode is keyed $11 (never in the intro)
        lda #$11
        bne ?ma
.if 1
?mset   sta last_scol               ; the mode fields for this shape's colour (out of
        ldy slen_hi                 ;   line: a shape usually keeps the previous one)
        sty BCB+BCB_WIDTH+1
        cmp #$11
        bcs ?mcp
        ldy #0
        sty BCB+BCB_AND
        cmp #$10
        beq ?mtr
        sta BCB+BCB_XOR             ; solid : AND 0, XOR = colour, copy mode
        lda #BLT_COPY
        sta BCB+BCB_CTRL
        jmp ?mok
?mtr    lda #$08                    ; transparent : dest |= 8
        sta BCB+BCB_XOR
        lda #BLT_OR
        sta BCB+BCB_CTRL
        jmp ?mok
?mcp    lda #0                      ; copy : src page 0, step 1, AND $FF, XOR 0
        sta BCB+BCB_SRC_ADDR+2
        sta BCB+BCB_XOR
        lda #1
        sta BCB+BCB_SRC_STEPX
        lda #$FF
        sta BCB+BCB_AND
        lda #BLT_COPY
        sta BCB+BCB_CTRL
        jmp ?mok
.endif
?hneg   inc i_idx                   ; h < 0 : nothing drawn and the edges untouched;
        dec j_idx                   ;   its steps / low words would be dead (every
        jmp ?seg                    ;   drawn segment recomputes them first)
?hzro   lda #1                      ; h == 0 : hh = 1, the steps, then the edges advance
        sta hh                      ;   once, no draw: ?tail becomes a jmp to ?hz2
        lda #$4C
        sta ?tail
        bne ?slopes                 ; (A = $4C: always)
?cont   ldx i_idx                   ; h = pts_y[i] - pts_y[i-1]
        lda pts_ylo,x
        sec
        sbc pts_ylo-1,x
        tay                         ; Y = h lo
        lda pts_yhi,x
        sbc pts_yhi-1,x
        bmi ?hneg
        bne ?hpos                   ; h >= 256 : hh = row_cnt = h & $FF, as before
        tya
        beq ?hzro
?hpos   sty hh
        sty row_cnt                 ; h > 0 : draw h scanlines
?slopes
        ; ---- right edge: dv = pts_x[j] - pts_x[j+1], step = dv * recip[hh] (16.16)
        ldx j_idx
        lda pts_xlo,x
        sec
        sbc pts_xlo+1,x
        tay                         ; Y = dv lo
        lda pts_xhi,x
        sbc pts_xhi+1,x
        sta dvsign                  ; bit 7 = the sign of dx
        bpl ?rab
        tya                         ; |dx| = -dv lo (|dx| < 256: the low byte is the
        eor #$FF                    ;   whole magnitude)
        tay
        iny
?rab   ldx hh                      ; X = dy
        cpx #1
        jeq fpi_rare.rh1
        sty ?ra1+1                ; |dx| into both products' square operands
        sty ?ra2+1
        sty ?rb1+1
        sty ?rb2+1
        tya
        eor #$FF
        sta ?ra3+1
        sta ?ra4+1
        sta ?rb3+1
        sta ?rb4+1
        ldy recip_lo,x              ; p0 = |dx| * recip_lo
        sec
?ra1  lda fmul_sq1l,y
?ra3  sbc fmul_sq2l,y
        sta g_N0
?ra2  lda fmul_sq1h,y
?ra4  sbc fmul_sq2h,y
        sta N1
        ldy recip_hi,x              ; p1 = |dx| * recip_hi
        sec
?rb1  lda fmul_sq1l,y
?rb3  sbc fmul_sq2l,y
        tax                         ; X = p1 lo
?rb2  lda fmul_sq1h,y
?rb4  sbc fmul_sq2h,y
        tay                         ; Y = p1 hi
        txa                         ; N = p0 + (p1 << 8)
        clc
        adc N1
        sta N1
        tya
        adc #0
        sta g_N2
        bit dvsign
        jmi fpi_rare.rng
?rps  lda g_N0                    ; step and step*2 in one carry chain
        sta fill_poly_int.smc_cr0+1
        asl @
        sta fill_poly_int.smc2_cr0+1
        lda N1
        sta fill_poly_int.smc_cr1+1
        rol @
        sta fill_poly_int.smc2_cr1+1
        lda g_N2
        sta fill_poly_int.smc_cr2+1
        rol @
        sta fill_poly_int.smc2_cr2+1
        lda #0
        sta fill_poly_int.smc_cr3+1
        rol @
        sta fill_poly_int.smc2_cr3+1
st_rdn
        ; ---- left edge: dv = pts_x[i] - pts_x[i-1], step = dv * recip[hh] (16.16)
        ldx i_idx
        lda pts_xlo,x
        sec
        sbc pts_xlo-1,x
        tay                         ; Y = dv lo
        lda pts_xhi,x
        sbc pts_xhi-1,x
        sta dvsign                  ; bit 7 = the sign of dx
        bpl ?lab
        tya                         ; |dx| = -dv lo (|dx| < 256: the low byte is the
        eor #$FF                    ;   whole magnitude)
        tay
        iny
?lab   ldx hh                      ; X = dy
        cpx #1
        jeq fpi_rare.lh1
        sty ?la1+1                ; |dx| into both products' square operands
        sty ?la2+1
        sty ?lb1+1
        sty ?lb2+1
        tya
        eor #$FF
        sta ?la3+1
        sta ?la4+1
        sta ?lb3+1
        sta ?lb4+1
        ldy recip_lo,x              ; p0 = |dx| * recip_lo
        sec
?la1  lda fmul_sq1l,y
?la3  sbc fmul_sq2l,y
        sta g_N0
?la2  lda fmul_sq1h,y
?la4  sbc fmul_sq2h,y
        sta N1
        ldy recip_hi,x              ; p1 = |dx| * recip_hi
        sec
?lb1  lda fmul_sq1l,y
?lb3  sbc fmul_sq2l,y
        tax                         ; X = p1 lo
?lb2  lda fmul_sq1h,y
?lb4  sbc fmul_sq2h,y
        tay                         ; Y = p1 hi
        txa                         ; N = p0 + (p1 << 8)
        clc
        adc N1
        sta N1
        tya
        adc #0
        sta g_N2
        bit dvsign
        jmi fpi_rare.lng
?lps  lda g_N0                    ; step and step*2 in one carry chain
        sta fill_poly_int.smc_cl0+1
        asl @
        sta fill_poly_int.smc2_cl0+1
        lda N1
        sta fill_poly_int.smc_cl1+1
        rol @
        sta fill_poly_int.smc2_cl1+1
        lda g_N2
        sta fill_poly_int.smc_cl2+1
        rol @
        sta fill_poly_int.smc2_cl2+1
        lda #0
        sta fill_poly_int.smc_cl3+1
        rol @
        sta fill_poly_int.smc2_cl3+1
st_ldn
        inc i_idx
        dec j_idx
        lda #$FF                    ; cr low word = 0x7FFF
        sta cr0
        lda #$7F
        sta cr1
        lda #$00                    ; cl low word = 0x8000
        sta cl0
        lda #$80
        sta cl1
?tail
fpi_tail bit ?hz2                    ; SMC opcode: bit (h > 0) / jmp (h == 0, ?hzro)
.if 1
pbh_4   lda #0  ; SMC = poly_bcb_h; 0 = full detail -> the classic 1x loop below;
.else
        lda poly_bcb_h              ; 0 = full detail -> the classic 1x loop below;
.endif
        jeq ?row                    ;   1 = half detail -> the PAIRED loop
        ; --- HALF-detail PAIRED row loop -------------------------------------
        ; Half mode draws the even rows as 2-tall spans, so the edges only have to
        ; land on those rows: advance them ONCE per drawn row with the DOUBLED steps
        ; (the smc2_* chain) instead of stepping + parity-testing every scanline. The
        ; parity is the ABSOLUTE one (hy_lo bit 0) the intro has always used, so
        ; exactly the same rows are drawn; the odd entry row is consumed by a single
        ; 1x step (adv_edges1). The rows left count as PAIRS (npair) + an odd last one.
rh_row  lda hy_lo
        and #1
        beq rh_ent                  ; even entry
        jsr adv_edges1              ; odd entry: row NOT drawn; consume 1 row
        inc hy_lo
        bne ?rhp1
        inc hy_hi
?rhp1   dec row_cnt
        bne ?rhp2
        jmp ?seg                    ; that was the segment's only row
?rhp2   lda hy_hi                   ; y-bound after the normalisation row
        bmi rh_ent
        bne ?retal2
        lda hy_lo
        cmp #SCRH
        bcs ?retal2                 ; past the bottom -> whole shape done
rh_ent  lda row_cnt                 ; pairs, and row_cnt's bit 0 (read back at the end)
        lsr @
        beq ?rhlast                 ; 0 or 1 row (row_cnt = 0 is one row, as before)
        sta npair
rh_top
smc_dsh jsr draw_scanline           ; draw the parity-0 row (PATCHED like smc_dsl)
        clc                         ; advance BOTH rows at once: doubled steps
        lda cr0
smc2_cr0 adc #0
        sta cr0
        lda cr1
smc2_cr1 adc #0
        sta cr1
        lda cr2
smc2_cr2 adc #0
        sta cr2
        lda cr3
smc2_cr3 adc #0
        sta cr3
        clc
        lda cl0
smc2_cl0 adc #0
        sta cl0
        lda cl1
smc2_cl1 adc #0
        sta cl1
        lda cl2
smc2_cl2 adc #0
        sta cl2
        lda cl3
smc2_cl3 adc #0
        sta cl3
        lda hy_lo                   ; hy += 2, kept in A for the y-bound
        clc
        adc #2
        sta hy_lo
        bcs ?rhc                    ; (rare: hy crosses a 256 boundary)
?rh2    dec npair
        beq ?rhend
        ldx hy_hi                   ; y-bound once per pair (A = hy_lo)
        bmi rh_top
        bne ?retal2
        cmp #SCRH
rh_end  bcc rh_top
?retal2 rts                         ; past the bottom -> whole shape done
?rhc    inc hy_hi                   ; out of line: the hy += 2 carry (C stays 1)
        bcs ?rh2
?rhend  lda row_cnt                 ; the pairs are done: an even count consumed the
        lsr @                       ;   segment exactly ...
        bcc ?toseg
        lda hy_hi                   ; ... an odd one: the y-bound, then the last row
        bmi ?rhlast
        bne ?retal2
        lda hy_lo
        cmp #SCRH
        bcc ?rhlast
        rts
?toseg  jmp ?seg
?rhlast
rh_last                             ; 1 row left, parity 0: draw, advance 1x
smc_dsi jsr draw_scanline
        jsr adv_edges1
        inc hy_lo
        beq ?rhli                   ; (rare: hy crosses a 256 boundary)
        jmp ?seg
?rhli   inc hy_hi
        jmp ?seg
        nocross fr_row, fr_row, fr_end   ; the 1x loop's back edge in one page
?row                                ; half-res gate (poly_bcb_h): full=0 -> AND=0 -> draw every
fr_row
.ifdef HIRES_CAP
        lda rpar                    ; row ; half=1 -> draw poly rows 0,2,4.. (relative parity)
.else
        lda hy_lo                   ; intro (frozen): absolute-even parity
.endif
.if 1
pbh_5   and #0  ; SMC = poly_bcb_h;   the span is 2 scanlines tall, so the skipped row is
.else
        and poly_bcb_h              ;   the span is 2 scanlines tall, so the skipped row is
.endif
        bne ?skipdr                 ;   covered too
smc_dsl jsr draw_scanline           ; operand PATCHED per shape by the intro's do_fill
                                    ;   (fill_poly_int.smc_dsl+1): bbox fully on-screen
                                    ;   -> draw_scanline_fast (no y test, no X clip).
                                    ;   The game fork never patches -> always clip.
?skipdr clc                         ; inline add_steps : steps are SMC immediates
        lda cr0                     ;   (adc #imm, 2 cyc, vs adc zp 3) patched/seg
smc_cr0 adc #0
        sta cr0
        lda cr1
smc_cr1 adc #0
        sta cr1
        lda cr2
smc_cr2 adc #0
        sta cr2
        lda cr3
smc_cr3 adc #0
        sta cr3
        clc
        lda cl0
smc_cl0 adc #0
        sta cl0
        lda cl1
smc_cl1 adc #0
        sta cl1
        lda cl2
smc_cl2 adc #0
        sta cl2
        lda cl3
smc_cl3 adc #0
        sta cl3
        inc hy_lo
        bne ?hc
        inc hy_hi
?hc     lda hy_hi
        bmi ?krow                   ; hy < 0 (still above the top) -> keep scanning
        bne ?retall                 ; hy >= 256 -> past the bottom, done
        lda hy_lo
        cmp #SCRH
        bcs ?retall                 ; hy >= 200 -> past the bottom, done
?krow
.ifdef HIRES_CAP
        lda rpar                    ; flip per-poly row parity each scanline (incl. off-screen)
        eor #1
        sta rpar
.endif
        dec row_cnt
        bne ?row
fr_end
?segnext
        jmp ?seg
?retall rts
?hz2    lda #$2C                    ; h == 0 : ?tail back to bit, then the edges
        sta ?tail                   ;   advance once (adv_edges1: clc first)
        jsr adv_edges1
        jmp ?seg
.endp

.if 1
; adv_edges1 : advance both edge accumulators by the 1x steps (reading the SMC
;   operands as data). Used by the paired loop's odd-entry / odd-exit single rows.
;   Clobbers A only.
.proc adv_edges1
        clc
        lda cr0
        adc fill_poly_int.smc_cr0+1
        sta cr0
        lda cr1
        adc fill_poly_int.smc_cr1+1
        sta cr1
        lda cr2
        adc fill_poly_int.smc_cr2+1
        sta cr2
        lda cr3
        adc fill_poly_int.smc_cr3+1
        sta cr3
        clc
        lda cl0
        adc fill_poly_int.smc_cl0+1
        sta cl0
        lda cl1
        adc fill_poly_int.smc_cl1+1
        sta cl1
        lda cl2
        adc fill_poly_int.smc_cl2+1
        sta cl2
        lda cl3
        adc fill_poly_int.smc_cl3+1
        sta cl3
        rts
.endp
.endif

; SMC_LD : the uniform byte distance between the cr and cl step-operand chains --
;   calc_step targets `smc_cr*+1,x` with X = 0 (right edge) or SMC_LD (left edge).
;   The erts pin the "uniform" assumption: if the ?row code is ever re-arranged so
;   the four deltas diverge, the build FAILS here instead of corrupting slopes.
SMC_LD  equ fill_poly_int.smc_cl0-fill_poly_int.smc_cr0
        ert [fill_poly_int.smc_cl1-fill_poly_int.smc_cr1]<>SMC_LD
        ert [fill_poly_int.smc_cl2-fill_poly_int.smc_cr2]<>SMC_LD
        ert [fill_poly_int.smc_cl3-fill_poly_int.smc_cr3]<>SMC_LD
        ert SMC_LD>255              ; must fit the abs,x index
.else
        lda #0
        sta i_idx
        lda nverts
        sec
        sbc #1
        sta j_idx                   ; j = n-1
        ldx #0                      ; hy = pts_y[0]
        lda pts_ylo,x
        sta hy_lo
        lda pts_yhi,x
        sta hy_hi
.ifdef HIRES_CAP
        lda #0                      ; per-polygon half-res row parity (relative to THIS poly's
        sta rpar                    ;   top, not absolute hy) -> every poly draws its 1st row as
.endif                             ;   a 2-tall span, so small polys are never dropped in half
                                   ;   mode (was: absolute-even -> 1px rocks on odd y vanished)
        ldx j_idx                   ; cr = (pts_x[j] + $8000) << 16  (X biased to
        lda #0                      ;   unsigned: +$8000 = +$80 in the integer hi byte,
        sta cr0                     ;   so the edge compare/clip can be unsigned)
        sta cr1
        lda pts_xlo,x
        sta cr2
        lda pts_xhi,x
        clc
        adc #$80
        sta cr3
        ldx #0                      ; cl = (pts_x[0] + $8000) << 16
        lda #0
        sta cl0
        sta cl1
        lda pts_xlo,x
        sta cl2
        lda pts_xhi,x
        clc
        adc #$80
        sta cl3
        inc i_idx                   ; i=1 ; j=n-2
        dec j_idx
        lda nverts
        sta numv
?seg    lda numv
        sec
        sbc #2
        sta numv
        bne ?cont
        rts
?cont
        ; h = pts_y[i] - pts_y[i-1]
        ldx i_idx
        lda pts_ylo,x
        sta hgt_lo
        lda pts_yhi,x
        sta hgt_hi
        dex
        lda hgt_lo
        sec
        sbc pts_ylo,x
        sta hgt_lo
        lda hgt_hi
        sbc pts_yhi,x
        sta hgt_hi
        ; dvr = pts_x[j] - pts_x[j+1]
        ldx j_idx
        lda pts_xlo,x
        sta dvr_lo
        lda pts_xhi,x
        sta dvr_hi
        inx
        lda dvr_lo
        sec
        sbc pts_xlo,x
        sta dvr_lo
        lda dvr_hi
        sbc pts_xhi,x
        sta dvr_hi
        ; dvl = pts_x[i] - pts_x[i-1]
        ldx i_idx
        lda pts_xlo,x
        sta dvl_lo
        lda pts_xhi,x
        sta dvl_hi
        dex
        lda dvl_lo
        sec
        sbc pts_xlo,x
        sta dvl_lo
        lda dvl_hi
        sbc pts_xhi,x
        sta dvl_hi
        ; hh = (h>0) ? h : 1
        lda hgt_hi
        bmi ?hh1
        lda hgt_hi
        ora hgt_lo
        bne ?hhp
?hh1    lda #1
        sta hh
        jmp ?slopes
?hhp    lda hgt_lo
        sta hh
?slopes
        lda dvr_lo
        sta dv_lo
        lda dvr_hi
        sta dv_hi
        jsr calc_step               ; step_r -> patch the ?row SMC adc operands
        lda N0
        sta smc_cr0+1
        lda N1
        sta smc_cr1+1
        lda N2
        sta smc_cr2+1
        lda N3
        sta smc_cr3+1
        lda dvl_lo
        sta dv_lo
        lda dvl_hi
        sta dv_hi
        jsr calc_step               ; step_l -> patch the ?row SMC adc operands
        lda N0
        sta smc_cl0+1
        lda N1
        sta smc_cl1+1
        lda N2
        sta smc_cl2+1
        lda N3
        sta smc_cl3+1
        inc i_idx
        dec j_idx
        lda #$FF                    ; cr low word = 0x7FFF
        sta cr0
        lda #$7F
        sta cr1
        lda #$00                    ; cl low word = 0x8000
        sta cl0
        lda #$80
        sta cl1
        lda hgt_hi                  ; h == 0 ?
        bne ?hnz
        lda hgt_lo
        bne ?hnz
        ; h==0 : advance edges once, no draw. The steps live in the SMC operands
        ; (patched above), so read them as data here (no separate str/stl ZP).
        clc
        lda cr0
        adc smc_cr0+1
        sta cr0
        lda cr1
        adc smc_cr1+1
        sta cr1
        lda cr2
        adc smc_cr2+1
        sta cr2
        lda cr3
        adc smc_cr3+1
        sta cr3
        clc
        lda cl0
        adc smc_cl0+1
        sta cl0
        lda cl1
        adc smc_cl1+1
        sta cl1
        lda cl2
        adc smc_cl2+1
        sta cl2
        lda cl3
        adc smc_cl3+1
        sta cl3
        jmp ?seg
?hnz    lda hgt_hi
        bmi ?segnext                ; h < 0 : skip
        lda hgt_lo
        sta row_cnt                 ; h > 0 : draw h scanlines
?row                                ; half-res gate (poly_bcb_h): full=0 -> AND=0 -> draw every
.ifdef HIRES_CAP
        lda rpar                    ; row ; half=1 -> draw poly rows 0,2,4.. (relative parity)
.else
        lda hy_lo                   ; intro (frozen): absolute-even parity
.endif
        and poly_bcb_h              ;   the span is 2 scanlines tall, so the skipped row is
        bne ?skipdr                 ;   covered too
smc_dsl jsr draw_scanline           ; operand PATCHED per shape by the intro's do_fill
                                    ;   (fill_poly_int.smc_dsl+1): bbox fully on-screen
                                    ;   -> draw_scanline_fast (no y test, no X clip).
                                    ;   The game fork never patches -> always clip.
?skipdr clc                         ; inline add_steps : steps are SMC immediates
        lda cr0                     ;   (adc #imm, 2 cyc, vs adc zp 3) patched/seg
smc_cr0 adc #0
        sta cr0
        lda cr1
smc_cr1 adc #0
        sta cr1
        lda cr2
smc_cr2 adc #0
        sta cr2
        lda cr3
smc_cr3 adc #0
        sta cr3
        clc
        lda cl0
smc_cl0 adc #0
        sta cl0
        lda cl1
smc_cl1 adc #0
        sta cl1
        lda cl2
smc_cl2 adc #0
        sta cl2
        lda cl3
smc_cl3 adc #0
        sta cl3
        inc hy_lo
        bne ?hc
        inc hy_hi
?hc     lda hy_hi
        bmi ?krow                   ; hy < 0 (still above the top) -> keep scanning
        bne ?retall                 ; hy >= 256 -> past the bottom, done
        lda hy_lo
        cmp #SCRH
        bcs ?retall                 ; hy >= 200 -> past the bottom, done
?krow
.ifdef HIRES_CAP
        lda rpar                    ; flip per-poly row parity each scanline (incl. off-screen)
        eor #1
        sta rpar
.endif
        dec row_cnt
        bne ?row
?segnext
        jmp ?seg
?retall rts
.endp
.endif


; draw_scanline : if hy in [0,199] emit the span [min(xl,xr),max] clipped to
;   [0,319], converted to page coords (LR halves x).
.if 1
; pbh_set : A -> poly_bcb_h AND every SMC operand that holds it (6502 skill: a
;   variable as the operand of lda # / and #). Called once, by detect_cpu.
pbh_set sta poly_bcb_h
        sta pbh_3+1
        sta fill_poly_int.pbh_4+1
        sta fill_poly_int.pbh_5+1
        lsr @                       ; C = the mode bit (X/Y untouched)
        lda #$4C                    ; 0 = full: $4C jmp = take the hop
        bcc ?pst
        lda #$2C                    ; 1 = half: $2C bit = fall through
?pst    sta fpi_rare.pbh_1          ; fpi_rare's two mode HOPS are patched
        sta fpi_rare.pbh_2          ;   OPCODES; their operands never change
        lda poly_bcb_h              ; A = the mode again for the caller
        rts
.endif
.if 1
        nocross dsl_r0, draw_scanline.ds_xb, draw_scanline.dsl_xge   ; the common hop
dsl_r0  rts                         ; the off-screen row exits of the entry test: a
                                    ;   backward branch in the entry's page (?ret is
                                    ;   past it)
.proc draw_scanline
        lda hy_hi                   ; (skill pass: fall-through on the in-range path,
        bne dsl_r0                  ;   one shared rts -- no branch-over-rts pairs)
        lda hy_lo
        cmp #SCRH
        bcs dsl_r0
dsl_body sta sy
        ; xr = high word of cr = cr2:cr3 ; xl = high word of cl = cl2:cl3. Both are
        ; ZP (the edge accumulators) and stable here (add_steps runs AFTER the draw),
        ; so compare/assign them DIRECTLY -- no xr/xl copy block needed.
        sec                         ; UNSIGNED compare xl - xr (X biased) ; BCC -> xl < xr
        lda cl2
        sbc cr2
        lda cl3
        sbc cr3
        bcs dsl_xge                 ; xl >= xr: the common order (one short taken hop)
ds_xb   lda cr2                     ; xl < xr (rare): a=xl, b=xr
        sta b_lo
        lda cl2
        sta a_lo
        ldx cl3
        stx a_hi
        lda cr3
        sta b_hi
        bcc ?clip                   ; C = 0 (the bcs above not taken)
dsl_xge lda cl2                     ; xl >= xr : a=xr, b=xl
        sta b_lo
        lda cr2
        sta a_lo
        ldx cr3
        stx a_hi
        lda cl3
        sta b_hi
?clip   ; A = b_hi, X = a_hi through the tests (no reloads). Biased coords: real 0 =
        ; $8000, real 319 = $813F, all UNSIGNED. An on-screen span runs every test
        ; NOT taken; the rare clips sit out of line below and come back.
        cpx #$81
        bcs ?ahi                    ; a_hi >= $81 (rare): a may be past 319
?aok    cmp #$80                    ; if b < $8000 (b_real < 0) skip
        bcc ?ret
        cmp #$81
        bcs ?bhi                    ; b_hi >= $81 (rare): clip b to $813F
?bok    cpx #$80
        bcc ?alo                    ; a_hi < $80 (rare): clip a to $8000
ds_rdy
?ready  jmp draw_scanline_fast.es_lrf  ; the fused clipped span (text keeps emit_span)
?ahi    bne ?ret                    ; a_hi > $81 -> a >= $8200 -> skip
        ldy a_lo
        cpy #$40
        bcc ?aok                    ; a <= $813F -> on
?ret    rts                         ; a >= $8140 (real >= 320) -> skip
?bhi    bne ?bmax                   ; b_hi > $81 -> clip
        ldy b_lo
        cpy #$40
        bcc ?bok                    ; b <= $813F -> ok
?bmax   lda #$3F
        sta b_lo
        lda #$81
        sta b_hi
        bne ?bok                    ; (A = $81)
?alo    lda #$00
        sta a_lo
        lda #$80
        sta a_hi
        bne ?ready                  ; (A = $80)
ds_end
.endp
.else
.proc draw_scanline
        lda hy_hi
        beq ?inr1
        rts
?inr1   lda hy_lo
        cmp #SCRH
        bcc dsl_body
        rts
dsl_body sta sy
        ; xr = high word of cr = cr2:cr3 ; xl = high word of cl = cl2:cl3. Both are
        ; ZP (the edge accumulators) and stable here (add_steps runs AFTER the draw),
        ; so compare/assign them DIRECTLY -- no xr/xl copy block needed.
        sec                         ; UNSIGNED compare xl - xr (X biased) ; BCC -> xl < xr
        lda cl2
        sbc cr2
        lda cl3
        sbc cr3
        bcc ?xll
        lda cr2                     ; xl >= xr : a=xr, b=xl
        sta a_lo
        lda cr3
        sta a_hi
        lda cl2
        sta b_lo
        lda cl3
        sta b_hi
        jmp ?clip
?bail   jmp ?ret
?xll    lda cl2                     ; xl < xr : a=xl, b=xr
        sta a_lo
        lda cl3
        sta a_hi
        lda cr2
        sta b_lo
        lda cr3
        sta b_hi
?clip   ; biased coords: real 0 = $8000, real 319 = $813F.  All UNSIGNED.
        lda a_hi                    ; if a > $813F (a_real > 319) skip
        cmp #$81
        bcc ?aok                    ; a_hi < $81 -> a <= $80FF (or <$8000, clipped below)
        bne ?bail                   ; a_hi > $81 -> a >= $8200 -> skip
        lda a_lo
        cmp #$40
        bcs ?bail                   ; a >= $8140 (real >= 320) -> skip
?aok    lda b_hi                    ; if b < $8000 (b_real < 0) skip
        cmp #$80
        bcc ?bail
        lda a_hi                    ; clip a to >= $8000 (a_real >= 0)
        cmp #$80
        bcs ?bclip
        lda #$00
        sta a_lo
        lda #$80
        sta a_hi
?bclip  lda b_hi                    ; clip b to <= $813F (b_real <= 319)
        cmp #$81
        bcc ?ready                  ; b_hi < $81 -> b <= $80FF -> ok
        bne ?bmax                   ; b_hi > $81 -> clip
        lda b_lo
        cmp #$40
        bcc ?ready                  ; b <= $813F -> ok
?bmax   lda #$3F
        sta b_lo
        lda #$81
        sta b_hi
?ready  jmp emit_span               ; tail-call (opt.md §1): emit_span->fill_span->fire_fill
                                    ;   all tail-call, so ONE rts returns straight to ?row
?ret    rts                         ; still the target of ?bail
.endp
.endif

; draw_scanline_yok : do_fill guarantees this shape is fully on-screen VERTICALLY (y0>=0
;   AND y1<=199), so skip draw_scanline's per-row y-test but KEEP the X-clip. Jumps into
;   draw_scanline's body past the y-test (dsl_body). Saves ~9 cyc/scanline on shapes that
;   need horizontal clipping but not vertical -- dispatched as the 3rd smc_dsl variant.
.proc draw_scanline_yok
        lda hy_lo
        jmp draw_scanline.dsl_body
.endp

; draw_scanline_fast : the no-clip variant, dispatched per shape via smc_dsl when
;   the shape's bbox is FULLY on-screen (intro: 75% of fills, 55% of scanlines;
;   vertices never leave the bbox -- verified over the whole intro, 0 violations).
;   hy is then always 0..199 (no y test) and both edges stay in [0,319] (no clip):
;   just order the endpoints and emit.
.if 1
.proc draw_scanline_fast
.if LORES
.ifdef HIRES_CAP
        ert 1                       ; this fused LR path has no runtime `hires` switch --
.endif                              ;   the GAME uses its own fork (src_game/aw_raster.asm)
        ; skill pass 2026-09-09 ("the value lives in A" / no store-then-shift-in-memory):
        ; the LR span is composed STRAIGHT from the edge accumulators -- sx = min(xl,xr)>>1
        ; and slen = (max>>1).lo - sx_lo are shifted in A and stored once into sx/slen.
        ; The old path copied the 4 endpoint bytes to a/b, then emit_span shifted a/b IN
        ; MEMORY (lsr/ror zp = 5 cyc each), copied a to sx and reloaded b. Same values,
        ; same rounding (lsr hi -> ror lo is the identical 16-bit shift). Dispatch goes
        ; through emit_span's cc_fsp jmp so any span patch still catches every span.
.if 1
        ; The span goes STRAIGHT into the span BCB: no sy / sx / slen round trip through
        ; zero page, no jmp cc_fsp / jmp fill_span, and no per-span colour test (the
        ; shape's mode went in at fill_poly_int). offset = row[hy] + (a>>1); the fields
        ; are written while the previous blit may still run -- only the START waits,
        ; hardened (Altirra vbxe.cpp: a BCB is read into registers at START).
        ldx hy_lo                   ; X = the row (0..199 on this path)
        sec                         ; UNSIGNED compare xl - xr (X biased) ; BCC -> xl < xr
        lda cl2
        sbc cr2
        lda cl3
        sbc cr3
        bcc dsf_xl
dsf_a   lda cr3                     ; xl >= xr : a = xr, b = xl
        lsr @
        tay                         ; Y = (a>>1) hi
        lda cr2
        ror @
        sta sx_lo                   ; (a>>1) lo -- the copy mode's source needs it
        clc
        adc row_lo,x
        sta BCB+BCB_DST_ADDR
        tya
        adc row_hi,x
        sta BCB+BCB_DST_ADDR+1
        lda cl3                     ; width-1 = (b>>1) - (a>>1)
        lsr @
        lda cl2
        ror @
dsf_b   sec
        sbc sx_lo
        sta BCB+BCB_WIDTH
dsf_w   lda VBXE_BL_BUSY            ; hardened wait right before the START (two reads:
        ora VBXE_BL_BUSY            ;   BUSY may read 0 for an instant between chained
        bne dsf_w                   ;   BCBs)
dsf_m   beq ?cp                     ; SMC opcode (fill_poly_int): bne = solid / transparent
        lda #1                      ;   (never taken) / beq = copy
        sta VBXE_BL_START
        rts
?cp     lda sx_lo                   ; copy from page 0 : src = the same offset
        clc
        adc row_lo,x
        sta BCB+BCB_SRC_ADDR
        tya
        adc row_hi,x
        sta BCB+BCB_SRC_ADDR+1
        lda #1
        sta VBXE_BL_START
        rts
dsf_xl  lda cl3                     ; xl < xr : a = xl, b = xr
        lsr @
        tay
        lda cl2
        ror @
        sta sx_lo
        clc
        adc row_lo,x
        sta BCB+BCB_DST_ADDR
        tya
        adc row_hi,x
        sta BCB+BCB_DST_ADDR+1
        lda cr3
        lsr @
        lda cr2
        ror @
        jmp dsf_b
; es_lrf : a clipped LR span from a_lo..b_hi (draw_scanline's tail) -- same fused tail.
es_lrf  ldx sy
        lda a_hi
        lsr @
        tay
        lda a_lo
        ror @
        sta sx_lo
        clc
        adc row_lo,x
        sta BCB+BCB_DST_ADDR
        tya
        adc row_hi,x
        sta BCB+BCB_DST_ADDR+1
        lda b_hi
        lsr @
        lda b_lo
        ror @
        jmp dsf_b
.else
        lda hy_lo
        sta sy                      ; (draw_scanline sets sy on its in-range path)
        sec                         ; UNSIGNED compare xl - xr (X biased) ; BCC -> xl < xr
        lda cl2
        sbc cr2
        lda cl3
        sbc cr3
        bcc ?xll
        lda cr3                     ; xl >= xr : a=xr, b=xl   -> sx = cr>>1
        lsr @
        sta sx_hi
        lda cr2
        ror @
        sta sx_lo
        lda cl3                     ; slen = (cl>>1).lo - sx_lo  (= width-1)
        lsr @
        lda cl2
        ror @
        sec
        sbc sx_lo
        sta slen_lo
        lda #0
        sta slen_hi
        jmp emit_span.cc_fsp        ; -> fill_span
?xll    lda cl3                     ; xl < xr : a=xl, b=xr   -> sx = cl>>1
        lsr @
        sta sx_hi
        lda cl2
        ror @
        sta sx_lo
        lda cr3                     ; slen = (cr>>1).lo - sx_lo
        lsr @
        lda cr2
        ror @
        sec
        sbc sx_lo
        sta slen_lo
        lda #0
        sta slen_hi
        jmp emit_span.cc_fsp
.endif
.else
        lda hy_lo
        sta sy                      ; (draw_scanline sets sy on its in-range path)
        sec                         ; UNSIGNED compare xl - xr (X biased) ; BCC -> xl < xr
        lda cl2
        sbc cr2
        lda cl3
        sbc cr3
        bcc ?xll
        lda cr2                     ; xl >= xr : a=xr, b=xl
        sta a_lo
        lda cr3
        sta a_hi
        lda cl2
        sta b_lo
        lda cl3
        sta b_hi
        jmp emit_span               ; tail-call, rts returns straight to ?row
?xll    lda cl2                     ; xl < xr : a=xl, b=xr
        sta a_lo
        lda cl3
        sta a_hi
        lda cr2
        sta b_lo
        lda cr3
        sta b_hi
        jmp emit_span
.endif
.endp
.else
.proc draw_scanline_fast
        lda hy_lo
        sta sy                      ; (draw_scanline sets sy on its in-range path)
        sec                         ; UNSIGNED compare xl - xr (X biased) ; BCC -> xl < xr
        lda cl2
        sbc cr2
        lda cl3
        sbc cr3
        bcc ?xll
        lda cr2                     ; xl >= xr : a=xr, b=xl
        sta a_lo
        lda cr3
        sta a_hi
        lda cl2
        sta b_lo
        lda cl3
        sta b_hi
        jmp emit_span               ; tail-call, rts returns straight to ?row
?xll    lda cl2                     ; xl < xr : a=xl, b=xr
        sta a_lo
        lda cl3
        sta a_hi
        lda cr2
        sta b_lo
        lda cr3
        sta b_hi
        jmp emit_span
.endp
.endif

; emit_span : a_lo:a_hi .. b_lo:b_hi (320-space, clipped) -> page span.
;   slen = byte_b - byte_a = WIDTH-1, exactly what the blitter BCB wants --
;   so no +1 here and no -1 in fill_span (saves both on every span).
.if 1
.proc emit_span
.ifdef HIRES_CAP
        ; GAME build: pick LR (>>1, $4000-biased LUT) or SR (no shift, $8000-biased LUT)
        ; at RUNTIME from `hires`. SR is the original .else (320) path verbatim.
        lda hires
        bne ?sr
        lda a_hi                    ; LR : byte_a = a>>1 -- shifted IN A, stored once
        lsr @                       ;   (skill pass; a/b are dead after this point)
        sta sx_hi
        lda a_lo
        ror @
        sta sx_lo
        lda b_hi                    ; byte_b = b>>1
        lsr @
        lda b_lo
        ror @
        sec
        sbc sx_lo
        sta slen_lo                 ; = width-1
        lda #0
        sta slen_hi
        beq ?col                    ; (A = 0 -> always taken)
?sr     lda a_lo                    ; SR : full 320-space col (a is $8000-biased)
        sta sx_lo
        lda a_hi
        sta sx_hi
        lda b_lo
        sec
        sbc a_lo
        sta slen_lo                 ; = width-1 (16-bit)
        lda b_hi
        sbc a_hi
        sta slen_hi
?col
.else
.if LORES
        lda a_hi                    ; byte_a = a>>1.  a is biased ($8000+) so a>>1 =
        lsr @                       ;   $4000 + col ; row_lut is pre-biased by -$4000.
        sta sx_hi                   ;   (skill pass: shifted IN A, stored once -- was
        lda a_lo                    ;   lsr/ror on a_hi/a_lo in memory + copy to sx;
        ror @                       ;   a/b are dead after this point)
        sta sx_lo
        lda b_hi                    ; byte_b = b>>1
        lsr @
        lda b_lo
        ror @
        sec
        sbc sx_lo
        sta slen_lo                 ; = width-1
        lda #0
        sta slen_hi
.else
        lda a_lo
        sta sx_lo
        lda a_hi
        sta sx_hi
        lda b_lo
        sec
        sbc a_lo
        sta slen_lo                 ; = width-1 (16-bit)
        lda b_hi
        sbc a_hi
        sta slen_hi
.endif
.endif
        ; PERF: scol is set ONCE per shape (fill_poly_int) / per text run (emit_run), so the
        ;   old per-span `lda poly_color / sta scol` here is gone (~5 cyc/span saved).
cc_fsp  jmp fill_span               ; operand SMC-patched -> bake_span by the GAME's
.endp                               ;   cell-cache during a bake (intro never patches)
.else
.proc emit_span
.ifdef HIRES_CAP
        ; GAME build: pick LR (>>1, $4000-biased LUT) or SR (no shift, $8000-biased LUT)
        ; at RUNTIME from `hires`. SR is the original .else (320) path verbatim.
        lda hires
        bne ?sr
        lsr a_hi                    ; LR : byte_a = a>>1
        ror a_lo
        lda a_lo
        sta sx_lo
        lda a_hi
        sta sx_hi
        lsr b_hi                    ; byte_b = b>>1
        ror b_lo
        lda b_lo
        sec
        sbc sx_lo
        sta slen_lo                 ; = width-1
        lda #0
        sta slen_hi
        jmp ?col
?sr     lda a_lo                    ; SR : full 320-space col (a is $8000-biased)
        sta sx_lo
        lda a_hi
        sta sx_hi
        lda b_lo
        sec
        sbc a_lo
        sta slen_lo                 ; = width-1 (16-bit)
        lda b_hi
        sbc a_hi
        sta slen_hi
?col
.else
.if LORES
        lsr a_hi                    ; byte_a = a>>1.  a is biased ($8000+) so a>>1 =
        ror a_lo                    ;   $4000 + col ; row_lut is pre-biased by -$4000.
        lda a_lo
        sta sx_lo
        lda a_hi
        sta sx_hi
        lsr b_hi                    ; byte_b = b>>1
        ror b_lo
        lda b_lo
        sec
        sbc sx_lo
        sta slen_lo                 ; = width-1
        lda #0
        sta slen_hi
.else
        lda a_lo
        sta sx_lo
        lda a_hi
        sta sx_hi
        lda b_lo
        sec
        sbc a_lo
        sta slen_lo                 ; = width-1 (16-bit)
        lda b_hi
        sbc a_hi
        sta slen_hi
.endif
.endif
        ; PERF: scol is set ONCE per shape (fill_poly_int) / per text run (emit_run), so the
        ;   old per-span `lda poly_color / sta scol` here is gone (~5 cyc/span saved).
cc_fsp  jmp fill_span               ; operand SMC-patched -> bake_span by the GAME's
.endp                               ;   cell-cache during a bake (intro never patches)
.endif

; draw_dots : degenerate polygon (n<3) -> plot each vertex as a 1-px span.
.proc draw_dots
        lda #0
        sta vidx
?l      ldx vidx
        lda pts_yhi,x
        bne ?nx
        lda pts_ylo,x
        cmp #SCRH
        bcs ?nx
        sta sy
        lda pts_xhi,x
        bne ?nx
        lda pts_xlo,x               ; x < 256 here, so always within 320-space
.ifdef HIRES_CAP
        ldy hires
        bne ?ddsr
        lsr @                       ; LR : col>>1, bias $4000
        sta sx_lo
        lda #$40
        sta sx_hi
        jmp ?ddw
?ddsr   sta sx_lo                   ; SR : col as-is, bias $8000
        lda #$80
        sta sx_hi
?ddw
.else
.if LORES
        lsr @
.endif
        sta sx_lo
        lda #>ROWBIAS               ; pts_x is unbiased here, but row_lut is pre-biased
        sta sx_hi                   ;   by -ROWBIAS, so add it back into sx
.endif
        lda #0                      ; slen = width-1 = 0 (1-px dot)
        sta slen_lo
        sta slen_hi
        lda poly_color
        sta scol
cc_dds  jsr fill_span               ; operand SMC-patched -> bake_span (see emit_span)
?nx     inc vidx
        lda vidx
        cmp nverts
        bne ?l
        rts
.endp
