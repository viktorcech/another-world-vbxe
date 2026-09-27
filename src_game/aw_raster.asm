;=============================================================================
; Integer 16.16 raster (mirror of fill_poly_int / _slope in tools/aw_sim.py)
;
;   GAME FORK (2026-07-02 fps wave) of src/aw_raster.asm -- the intro keeps the
;   original (frozen); awgame.asm icl's THIS file. Differences vs the intro:
;     * calc_step (src_game/aw_polygon.asm) writes the 4 step bytes DIRECTLY
;       into the ?row SMC adc-operands via abs,x (X = 0 for the cr chain,
;       SMC_LD for the cl chain) -- the 8-instruction lda N/sta smc copy block
;       per edge (~28 cyc) is gone. ert guards below pin the chain deltas.
;     * smc_yj: the per-row y-bounds test (?ytst, ~11 cyc/row) is SMC-skipped
;       for shapes whose bbox is fully on-screen vertically (the fast/yok clip
;       dispatch in do_fill already proves hy stays 0..199).
;   Output-identical: same adds, same order, same wrap behaviour.
;=============================================================================
; zero-page cells of the game raster (defined BEFORE use: a forward zp equate
; assembles as absolute in MADS's first pass). dv_lo / dv_hi are free since the
; inline step code keeps dx in Y.
npair   = dv_lo                         ; the half loop's pairs left
dsf_hh  = dv_hi                         ; draw_scanline_fast: hr's high byte
;=============================================================================
; two stretches in one page each (a taken branch across a page costs +1): the entry
; test's `bcs fpi_poly` (every shape), and the half-detail loop from the tail's
; `jeq ?row` through its odd-entry block to the last-row exit (every segment / row).
.if 1
        nocross2 fill_poly_int, fill_poly_int, fill_poly_int.fpi_poly+1, fill_poly_int.fpi_tail, fill_poly_int.smc_dsi+3
.endif
.if 1
.proc fill_poly_int
        lda fill_col
        sta poly_color
        sta scol                    ; PERF: scol is invariant for the whole shape -- set it
                                    ;   ONCE here (A still = fill_col) instead of reloading
                                    ;   poly_color->scol in emit_span on every span. (draw_dots
                                    ;   below sets its own scol; text uses emit_run, see there.)
        lda nverts
        cmp #3
        bcc ?dots                   ; n < 3 (rare): draw_dots, out of line
?poly
fpi_poly
.if 1
        ldx #$D0                    ; the shape's colour mode goes into the span BCB now
        lda scol                    ;   (no wait: the blitter reads a BCB only at START),
        cmp #$11                    ;   so the fused spans just START: dsf_m / dsr_m =
        bcc ?ma                     ;   bne (never taken) ...
        ldx #$F0                    ; ... or beq for the copy mode (+ the source per span)
        lda #$11                    ; (copy mode is keyed $11)
?ma     stx draw_scanline_fast.dsf_m
.ifdef HIRES_CAP
        stx dsf_sr.dsr_m
.endif
        cmp last_scol
        bne ?mset                   ; a new colour mode (out of line, above)
?mok
.endif
        lda #1
        sta i_idx                   ; i = 1 (the first segment is pts[0] -> pts[1])
        ldx nverts
        txa
        lsr @
        sta numv                    ; n/2 : one ?seg pass per segment, +1 (n is even:
        dex                         ;   an odd n never reached 0 -- a hang before too)
        lda #0                      ; per-polygon half-res row parity (relative to THIS poly's
        sta rpar                    ;   top, not absolute hy) -> every poly draws its 1st row as
                                    ;   a 2-tall span, so small polys are never dropped in half
        ; cr0/cr1/cl0/cl1 are not set here: n >= 3, so the first ?seg always reaches
        ; the per-segment reset below before any read. +$80 mod 256 = eor #$80 (C is dead).
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
.if 1
?mset   sta last_scol               ; = span_mode, inline (once a shape, out of line)
        ldy slen_hi
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
        sta hh                      ;   once, no draw: ?tail becomes `jmp ?hz2` for it
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
.if 1
        ; recip into the square operands, |dx| stays in Y: sq2[255-r+dx] ==
        ; sq2[255-dx+r] (= (dx-r)^2/4, 16-bit equal), and the sq2 page cross
        ; (+1, index >= 256) now needs |dx| > recip -- the rare side.
        lda recip_lo,x
        sta ?ra1+1
        sta ?ra2+1
        eor #$FF
        sta ?ra3+1
        sta ?ra4+1
        lda recip_hi,x
        sta ?rb1+1
        sta ?rb2+1
        eor #$FF
        sta ?rb3+1
        sta ?rb4+1
?ra1  lda fmul_sq1l,y             ; p0 = |dx| * recip_lo; C=1 from cpx #1
?ra3  sbc fmul_sq2l,y             ;   (hh >= 2 on this path), lda/sta/eor keep it
        sta g_N0
?ra2  lda fmul_sq1h,y
?ra4  sbc fmul_sq2h,y
        sta N1
        sec
?rb1  lda fmul_sq1l,y             ; p1 = |dx| * recip_hi
?rb3  sbc fmul_sq2l,y
        tax                         ; X = p1 lo
?rb2  lda fmul_sq1h,y
?rb4  sbc fmul_sq2h,y
        tay                         ; Y = p1 hi
.else
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
.endif
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
.if 1
        ; recip into the square operands, |dx| stays in Y: sq2[255-r+dx] ==
        ; sq2[255-dx+r] (= (dx-r)^2/4, 16-bit equal), and the sq2 page cross
        ; (+1, index >= 256) now needs |dx| > recip -- the rare side.
        lda recip_lo,x
        sta ?la1+1
        sta ?la2+1
        eor #$FF
        sta ?la3+1
        sta ?la4+1
        lda recip_hi,x
        sta ?lb1+1
        sta ?lb2+1
        eor #$FF
        sta ?lb3+1
        sta ?lb4+1
?la1  lda fmul_sq1l,y             ; p0 = |dx| * recip_lo; C=1 from cpx #1
?la3  sbc fmul_sq2l,y             ;   (hh >= 2 on this path), lda/sta/eor keep it
        sta g_N0
?la2  lda fmul_sq1h,y
?la4  sbc fmul_sq2h,y
        sta N1
        sec
?lb1  lda fmul_sq1l,y             ; p1 = |dx| * recip_hi
?lb3  sbc fmul_sq2l,y
        tax                         ; X = p1 lo
?lb2  lda fmul_sq1h,y
?lb4  sbc fmul_sq2h,y
        tay                         ; Y = p1 hi
.else
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
.endif
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
pbh_3   dta $2C                     ; SMC OPCODE (poly_bcb_h, pbh_set): $4C jmp =
        dta a(?row)                 ;   full detail -> the classic 1x loop; $2C bit
                                    ;   = half -> fall into PAIRED (operand fixed)
.else
        lda poly_bcb_h              ; 0 = full detail -> the classic 1x loop (placed
        jeq ?row                    ;   after this one); 1 = half detail -> PAIRED
.endif
        ; --- HALF-detail PAIRED row loop (poly_bcb_h = 1: stock 6502) ---------
        ; fps wave 2 (equivalence proof: tools/verify_halfstep.py, 8810 cases,
        ; bit-exact spans + cross-segment state): draw the parity-0 rows (2-tall
        ; spans, as before) but advance the edges ONCE per drawn row with the
        ; DOUBLED slopes (smc2_* chain, written by the step code) instead of stepping
        ; + parity-testing every scanline -- ~half the edge-walk cost exactly in
        ; the mode that struggles (arene/jail on a 1.79 MHz 6502). rpar keeps its
        ; poly-relative, cross-segment meaning via the odd-entry/odd-exit single
        ; rows (1x steps via adv_edges1). The y-bound runs once per PAIR: hy is
        ; monotonic, so any overrun spans are y-culled inside draw_scanline (the
        ; guarded output is identical -- see the tool's divergence policy).
?rowh
rh_row  lda rpar
        beq rh_ent                  ; even entry (parity 0)
        jsr adv_edges1              ; odd entry: row NOT drawn; consume 1 row,
        inc hy_lo                   ;   re-align to parity 0
        bne ?rhp1
        inc hy_hi
?rhp1   lda #0
        sta rpar
        dec row_cnt
        bne ?rhp2
        jmp ?seg                    ; that was the segment's only row
?rhp2   lda hy_hi                   ; y-bound after the normalization row
        bmi rh_ent
        bne ?retal2
        lda hy_lo
        cmp #SCRH
        bcs ?retal2                 ; past the bottom -> whole shape done
rh_ent  lda row_cnt                 ; the rows left, as PAIRS (npair) + an odd last one
        lsr @                       ;   (row_cnt's bit 0, read back at the end)
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
        lsr @                       ;   segment exactly (rpar stays 0) ...
        bcc ?toseg
        lda hy_hi                   ; ... an odd one: the y-bound, then the last row
        bmi ?rhlast
        bne ?retal2
        lda hy_lo
        cmp #SCRH
        bcc ?rhlast
        rts
?toseg  jmp ?seg                    ; (?seg is out of branch range)
?rhlast                             ; 1 row left, parity 0: draw, advance 1x,
smc_dsi jsr draw_scanline           ;   hand parity 1 to the next segment
        jsr adv_edges1
        inc hy_lo
        beq ?rhli                   ; (rare: hy crosses a 256 boundary)
?rhl1   lda #1
        sta rpar
        jmp ?seg
?rhli   inc hy_hi
        jmp ?rhl1
.if 1
        nocross fr_row, fr_row, yk_row+4    ; the full-detail loop's back edge in one page
.endif
        ; --- FULL-detail row loop (poly_bcb_h = 0: Rapidus, and SR 16008) ---
?row
fr_row
smc_dsl jsr draw_scanline           ; operand PATCHED per shape by do_fill's 3-way
                                    ;   clip dispatch: fast (no y-test, no X-clip),
                                    ;   yok (X-clip only) or the full draw_scanline
        clc                         ; inline add_steps : steps are SMC immediates
        lda cr0                     ;   (adc #imm, 2 cyc), written by the step code
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
        bne ?hyok
        inc hy_hi
?hyok
smc_yj  jmp yk_tst                  ; operand PATCHED per shape by do_fill: bbox fully
                                    ;   on-screen vertically -> yk_row (no y-test)
yk_tst  lda hy_hi
        bmi yk_row                  ; hy < 0 (still above the top) -> keep scanning
        bne ?retall                 ; hy >= 256 -> past the bottom, done
        lda hy_lo
        cmp #SCRH
        bcs ?retall                 ; hy >= 200 -> past the bottom, done
yk_row  dec row_cnt
        bne ?row
        jmp ?seg
?retall rts
?hz2    lda #$2C                    ; h == 0 : ?tail back to `bit`, then the edges
        sta ?tail                   ;   advance once (C still the step code's, as the
        jsr adv_edges1              ;   old path handed adv_edges1)
        jmp ?seg
.endp
.else
.proc fill_poly_int
        lda fill_col
        sta poly_color
        sta scol                    ; PERF: scol is invariant for the whole shape -- set it
                                    ;   ONCE here (A still = fill_col) instead of reloading
                                    ;   poly_color->scol in emit_span on every span. (draw_dots
                                    ;   below sets its own scol; text uses emit_run, see there.)
        lda nverts
        cmp #3
        bcs ?poly
        jmp draw_dots
?poly
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
        ldx #0                      ; step_r -> calc_step writes the ?row smc_cr*
        jsr calc_step               ;   operands DIRECTLY (fps wave; no N-copy)
        lda dvl_lo
        sta dv_lo
        lda dvl_hi
        sta dv_hi
        ldx #SMC_LD                 ; step_l -> the smc_cl* operands (same chain,
        jsr calc_step               ;   offset by the uniform cl-cr delta)
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
        jsr adv_edges1              ; h==0 : advance edges once (1x), no draw
        jmp ?seg
?hnz    lda hgt_hi
        bmi ?segnext                ; h < 0 : skip
        lda hgt_lo
        sta row_cnt                 ; h > 0 : draw h scanlines
        lda poly_bcb_h              ; fps wave 2: 0 = full detail -> classic 1x
        beq ?row                    ;   loop; 1 = half detail -> the PAIRED loop
        jmp ?rowh
        ; --- FULL-detail row loop (poly_bcb_h = 0: Rapidus, and SR 16008). The
        ; old per-row parity gate (lda rpar / and poly_bcb_h / bne) and the rpar
        ; flip are GONE from this loop -- it only runs when the mask is 0, so
        ; they were ~17 dead cycles per scanline.
?row
smc_dsl jsr draw_scanline           ; operand PATCHED per shape by do_fill's 3-way
                                    ;   clip dispatch: fast (no y-test, no X-clip),
                                    ;   yok (X-clip only) or the full draw_scanline
        clc                         ; inline add_steps : steps are SMC immediates
        lda cr0                     ;   (adc #imm, 2 cyc, vs adc zp 3), written by
smc_cr0 adc #0                      ;   calc_step per segment (fps wave: directly)
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
        bne ?hyok
        inc hy_hi
?hyok
smc_yj  jmp yk_tst                  ; operand PATCHED per shape by do_fill (fps
                                    ;   wave): bbox fully on-screen vertically ->
                                    ;   yk_row (skip the ~11-cyc y-test per row);
                                    ;   else yk_tst (the original bounds test)
yk_tst  lda hy_hi
        bmi yk_row                  ; hy < 0 (still above the top) -> keep scanning
        bne ?retall                 ; hy >= 256 -> past the bottom, done
        lda hy_lo
        cmp #SCRH
        bcs ?retall                 ; hy >= 200 -> past the bottom, done
yk_row  dec row_cnt
        bne ?row
?segnext
        jmp ?seg
?retall rts
        ; --- HALF-detail PAIRED row loop (poly_bcb_h = 1: stock 6502) ---------
        ; fps wave 2 (equivalence proof: tools/verify_halfstep.py, 8810 cases,
        ; bit-exact spans + cross-segment state): draw the parity-0 rows (2-tall
        ; spans, as before) but advance the edges ONCE per drawn row with the
        ; DOUBLED slopes (smc2_* chain, written by the step code) instead of stepping
        ; + parity-testing every scanline -- ~half the edge-walk cost exactly in
        ; the mode that struggles (arene/jail on a 1.79 MHz 6502). rpar keeps its
        ; poly-relative, cross-segment meaning via the odd-entry/odd-exit single
        ; rows (1x steps via adv_edges1). The y-bound runs once per PAIR: hy is
        ; monotonic, so any overrun spans are y-culled inside draw_scanline (the
        ; guarded output is identical -- see the tool's divergence policy).
?rowh   lda rpar
        beq ?rhpair                 ; even entry -> pair loop
        jsr adv_edges1              ; odd entry: row NOT drawn; consume 1 row,
        inc hy_lo                   ;   re-align to parity 0
        bne ?rhp1
        inc hy_hi
?rhp1   lda #0
        sta rpar
        dec row_cnt
        bne ?rhb0
        jmp ?seg                    ; that was the segment's only row
?rhb0   lda hy_hi                   ; y-bound after the normalization row
        bmi ?rhpair
        bne ?retal2
        lda hy_lo
        cmp #SCRH
        bcs ?retal2
?rhpair lda row_cnt
        cmp #2
        bcc ?rhlast                 ; exactly 1 row left (parity 0)
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
        inc hy_lo                   ; hy += 2
        bne ?rh2a
        inc hy_hi
?rh2a   inc hy_lo
        bne ?rh2b
        inc hy_hi
?rh2b   dec row_cnt
        dec row_cnt
        bne ?rhb1
        jmp ?seg                    ; consumed the segment exactly (rpar stays 0)
?rhb1   lda hy_hi                   ; y-bound once per pair
        bmi ?rhpair
        bne ?retal2
        lda hy_lo
        cmp #SCRH
        bcc ?rhpair
?retal2 rts                         ; past the bottom -> whole shape done
?rhlast                             ; 1 row left, parity 0: draw, advance 1x,
smc_dsi jsr draw_scanline           ;   hand parity 1 to the next segment
        jsr adv_edges1
        inc hy_lo
        bne ?rhl1
        inc hy_hi
?rhl1   lda #1
        sta rpar
        jmp ?seg
.endp
.endif

; adv_edges1 : advance both edge accumulators by the 1x steps (reading the SMC
;   operands as data). Shared by the h==0 segment path and the paired loop's
;   odd-entry / odd-exit single rows. Clobbers A only.
;   Relocated to the free $0F80 gap (game_text code ends ~$0B24, text data
;   starts $1000) -- the $2000-$3FFF chain is nearly full; `org` resumes after.
adv_edges1_resume equ *
.ifdef ADV_ORG
        org ADV_ORG
.endif
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
.if 1
; pbh_set : A -> poly_bcb_h AND every SMC operand that holds it (6502 skill: a
;   variable as the operand of lda # / adc #). Called when the detail level changes.
pbh_set sta poly_bcb_h
        sta pbh_4+1
        sta pbh_5+1
        sta cc_bake.pbh_6+1
        sta bake_span.pbh_7+1
        lsr @                       ; C = the mode bit (X/Y untouched: the
        lda #$4C                    ;   set_render_mode caller keeps state there)
        bcc ?pst                    ; 0 = full: $4C jmp = take the hop
        lda #$2C                    ; 1 = half: $2C bit = fall through
?pst    sta fpi_rare.pbh_1          ; the three mode HOPS are patched OPCODES;
        sta fpi_rare.pbh_2          ;   their operands never change
        sta fill_poly_int.pbh_3
        lda poly_bcb_h              ; A = the mode again for the caller
        rts
; pf_bank_hi : bank index -> bits 14-15 of the stream offset. Read as
;   pf_bank_hi-$80-PLAY_BANK0,x (x = the MEMAC-B bank byte): its low byte must be
;   >= $80+PLAY_BANK0 so that base and entry share a page (no +1 per read).
pf_bank_hi dta $00,$40,$80,$C0
        ert [<pf_bank_hi]<[$80+PLAY_BANK0]
        ert [<pf_bank_hi]>$FC
.endif
.ifdef ADV_ORG
        ert *>$0FFF                 ; stay clear of the $1000 text-data segment
        org adv_edges1_resume       ; back to the $2000 chain
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
        ; the smc2 (doubled-step) chain must mirror the same delta -- calc_step
        ; writes it with the SAME ,x offset
        ert [fill_poly_int.smc2_cl0-fill_poly_int.smc2_cr0]<>SMC_LD
        ert [fill_poly_int.smc2_cl1-fill_poly_int.smc2_cr1]<>SMC_LD
        ert [fill_poly_int.smc2_cl2-fill_poly_int.smc2_cr2]<>SMC_LD
        ert [fill_poly_int.smc2_cl3-fill_poly_int.smc2_cr3]<>SMC_LD

; draw_scanline : if hy in [0,199] emit the span [min(xl,xr),max] clipped to
;   [0,319], converted to page coords (LR halves x).
.if 1
; the common endpoint order's hop (bcs dsl_xge, taken on every span) must not
; straddle a page: pad in front of the proc when it would.
DSL_LEN = draw_scanline.dsl_xge+1-draw_scanline
DSL_O   = *&$FF
.if DSL_O+DSL_LEN>$100
:[$100-DSL_O] dta 0
.endif
.endif
.if 1
.proc draw_scanline
        lda hy_hi                   ; (skill pass: fall-through on the in-range path,
        bne ?ret                    ;   one shared rts -- no branch-over-rts pairs)
        lda hy_lo
        cmp #SCRH
        bcs ?ret
dsl_body sta sy
        ; xr = high word of cr = cr2:cr3 ; xl = high word of cl = cl2:cl3. Both are
        ; ZP (the edge accumulators) and stable here (add_steps runs AFTER the draw),
        ; so compare/assign them DIRECTLY -- no xr/xl copy block needed.
        sec                         ; UNSIGNED compare xl - xr (X biased) ; BCC -> xl < xr
        lda cl2
        sbc cr2
        lda cl3
        sbc cr3
        bcc ds_xb                   ; xl < xr (rare, 0/635 in the profile): out of
                                    ;   line below, comes back to ?clip
dsl_xge lda cl2                     ; xl >= xr : a=xr, b=xl (common: falls through)
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
?ready
dsl_rdy jmp draw_scanline_fast.es_lrf ; operand PATCHED by set_render_mode: es_lrf
                                    ;   (LR) / dsf_sr.es_srf (SR), es_lr while baking
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
ds_xb   lda cr2                     ; xl < xr (rare): a=xl, b=xr; ?clip needs only
        sta b_lo                    ;   A = b_hi, X = a_hi (it sets its own flags)
        lda cl2
        sta a_lo
        ldx cl3
        stx a_hi
        lda cr3
        sta b_hi
        jmp ?clip
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
; The fused LR / SR spans: the span goes straight into the span BCB -- no sx / sy / slen
; round-trip through zero page, no jmp cc_fsp / jmp fill_span, no per-span colour test.
; fill_poly_int writes the shape's colour mode into the BCB up front (the blitter reads
; a BCB only at START -- Altirra vbxe.cpp LoadBlitter / LoadBCB -- so no wait is needed
; for the fields); each span writes its address + width, waits (hardened) and STARTs;
; the copy mode also writes its source (dsf_m / dsr_m = beq, armed per shape).
; Edges ordered and halved on the HALVES: x>>1 is compared on its low byte when both
; halves share the high byte (always on this path: both edges are in [$8000,$813F]) --
; equal halves give width 0 and the same address either way, so it is the full compare's
; answer. The high byte test is an eor (C, the ror bit, survives it); a differing one
; (only an edge outside 0..319: never in the game data, tools/_probe_dsf.py) takes the
; full 16-bit compare out of line, the original code. The usual order (xl >= xr, a = hr)
; writes the address first.
DSF_B2  = $A5                           ; the 3rd byte (lda zp) put back by the un-patchers
        nocross draw_scanline_fast, draw_scanline_fast.dsf_b, draw_scanline_fast.dsf_xl
.proc draw_scanline_fast
        ; LR, bbox fully on-screen. The first 3 bytes are patched to `jmp dsf_sr` in SR
        ; (set_render_mode) and to `jmp dsf_bake` while the cell cache bakes.
        ldx hy_lo                   ; X = the row (0..199 on this path)
        lda cr3                     ; hr = xr >> 1
        lsr @
        sta dsf_hh
        tay                         ; Y = hr hi
        lda cr2
        ror @
        sta sx_lo
        clc                         ; dst = row + hr
        adc row_lo,x
        sta BCB+BCB_DST_ADDR
        tya
        adc row_hi,x
        sta BCB+BCB_DST_ADDR+1
        lda cl3                     ; hl = xl >> 1
        lsr @
        eor dsf_hh                  ; the same high byte? (C = the ror bit, kept)
        bne ?full
        lda cl2
        ror @
dsf_b   sec                         ; width-1 = hl - hr
        sbc sx_lo
        bcc dsf_xl
dsf_bw  sta BCB+BCB_WIDTH
dsf_w   lda VBXE_BL_BUSY            ; hardened wait right before the START (two reads:
        ora VBXE_BL_BUSY            ;   BUSY may read 0 for an instant between chained
        bne dsf_w                   ;   BCBs -- a cell blit may still run)
dsf_m   beq ?cp                     ; SMC opcode: bne (never taken) / beq (copy: Z = 1)
        lda #1
        sta VBXE_BL_START
        rts
?cp     lda BCB+BCB_DST_ADDR        ; copy from page 0 : src = the same offset, read
        sta BCB+BCB_SRC_ADDR        ;   back from the BCB (plain VRAM through MEMAC-A:
        lda BCB+BCB_DST_ADDR+1      ;   Altirra vbxe.cpp maps CPU reads and writes of
        sta BCB+BCB_SRC_ADDR+1      ;   the window alike)
        lda #1
        sta VBXE_BL_START
        rts
dsf_xl  eor #$FF                    ; xl < xr : width-1 = hr - hl (C = 0 here: the +1;
        adc #1                      ;   C = 0 after, hr - hl < 256)
        sta BCB+BCB_WIDTH
        lda cl3                     ; a = hl : the address again (Y = hr hi = hl hi)
        lsr @
        lda cl2
        ror @
        clc
        adc row_lo,x
        sta BCB+BCB_DST_ADDR
        tya
        adc row_hi,x
        sta BCB+BCB_DST_ADDR+1
        jmp dsf_w
?full   sec                         ; the halves' high bytes differ: the full compare
        lda cl2                     ;   of the edges (the original fast path)
        sbc cr2
        lda cl3
        sbc cr3
        bcc ?fxl
        lda cl3                     ; xl >= xr : a = xr (the address is written)
        lsr @
        lda cl2
        ror @
        sec
        sbc sx_lo
        jmp dsf_bw
?fxl    lda cl3                     ; xl < xr : a = xl, b = xr
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
        sec
        sbc sx_lo
        jmp dsf_bw
; es_lrf : a clipped LR span from a_lo..b_hi (draw_scanline's dsl_rdy in LR, the text
;   runs): width-1 = (b>>1) lo - (a>>1) lo as it comes, no ordering (a <= b)
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
        sec
        sbc sx_lo
        jmp dsf_bw
.endp

.ifdef CELLCACHE
; dsf_bake : the LR fast path through cc_fsp (bake_span) while the cell cache bakes.
.proc dsf_bake
        ; sx = min(xl,xr) >> 1, slen = (max >> 1) - sx_lo, shifted in A from the edge
        ; accumulators; slen_hi stays 0 in LR. Through cc_fsp (bake_span patch).
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
        jmp emit_span.cc_fsp        ; -> bake_span
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
        jmp emit_span.cc_fsp
.endp
.endif

.ifdef HIRES_CAP
; dsf_sr : the fused SR 320 span (part 16008): the fast path (no clip, no y-test) and,
;   at es_srf, the clipped one from a_lo..b_hi. offset = row2[y] + a (a $8000-biased,
;   the LUT -$8000), width-1 = b - a (16-bit).
        nocross dsf_sr, dsf_sr.dsr_a, dsf_sr.dsr_xl
.proc dsf_sr
        ldx hy_lo
        sec
        lda cl2
        sbc cr2
        lda cl3
        sbc cr3
        bcc dsr_xl
dsr_a   lda cr2                     ; xl >= xr : a = xr, b = xl
        sta a_lo
        clc
        adc row_lo2,x
        sta BCB+BCB_DST_ADDR
        lda cr3
        sta a_hi
        adc row_hi2,x
        sta BCB+BCB_DST_ADDR+1
        lda cl2
        sec
        sbc cr2
        sta BCB+BCB_WIDTH
        lda cl3
        sbc cr3
        sta BCB+BCB_WIDTH+1
dsr_w   lda VBXE_BL_BUSY            ; hardened wait right before the START
        ora VBXE_BL_BUSY
        bne dsr_w
dsr_m   beq ?cp                     ; SMC opcode: bne (never taken) / beq (copy: Z = 1)
        lda #1
        sta VBXE_BL_START
        rts
?cp     lda a_lo                    ; copy from page 0 : src = the same offset
        clc
        adc row_lo2,x
        sta BCB+BCB_SRC_ADDR
        lda a_hi
        adc row_hi2,x
        sta BCB+BCB_SRC_ADDR+1
        lda #1
        sta VBXE_BL_START
        rts
dsr_xl  lda cl2                     ; xl < xr : a = xl, b = xr
        sta a_lo
        clc
        adc row_lo2,x
        sta BCB+BCB_DST_ADDR
        lda cl3
        sta a_hi
        adc row_hi2,x
        sta BCB+BCB_DST_ADDR+1
        lda cr2
        sec
        sbc cl2
        sta BCB+BCB_WIDTH
        lda cr3
        sbc cl3
        sta BCB+BCB_WIDTH+1
        jmp dsr_w                   ; (a jmp never pays a page cross)
es_srf  ldx sy
        lda a_lo
        clc
        adc row_lo2,x
        sta BCB+BCB_DST_ADDR
        lda a_hi
        adc row_hi2,x
        sta BCB+BCB_DST_ADDR+1
        lda b_lo
        sec
        sbc a_lo
        sta BCB+BCB_WIDTH
        lda b_hi
        sbc a_hi
        sta BCB+BCB_WIDTH+1
        jmp dsr_w
.endp
.endif
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
        ; GAME build: LR (>>1, $4000-biased LUT) or SR (no shift, $8000-biased LUT) from
        ; `hires`. draw_scanline jumps straight to es_lr in LR (its dsl_rdy operand);
        ; text comes through here. slen_hi stays 0 in LR (set_render_mode).
        lda hires
        bne es_sr
es_lr   lda a_hi                    ; LR : byte_a = a>>1 -- shifted IN A, stored once
        lsr @
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
cc_fsp  jmp fill_span               ; operand SMC-patched: bake_span (cell-cache bake) /
                                    ;   fill_span_sr (set_render_mode, SR)
es_sr   lda a_lo                    ; SR : full 320-space col (a is $8000-biased)
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
        jmp cc_fsp
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
cc_fsp  jmp fill_span               ; operand SMC-patched -> bake_span by the GAME's
.endif                              ;   cell-cache during a bake (intro never patches)
.endp
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
        nocross draw_dots, draw_dots.dd_l, draw_dots.dd_nx
.proc draw_dots
        lda #0
        sta vidx
?l
dd_l    ldx vidx
        lda pts_yhi,x
        bne dd_nx
        lda pts_ylo,x
        cmp #SCRH
        bcs dd_nx
        sta sy
        lda pts_xhi,x
        bne dd_nx
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
dd_nx
?nx     inc vidx
        lda vidx
        cmp nverts
        bne ?l
        rts
.endp
