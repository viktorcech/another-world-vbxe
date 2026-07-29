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
        ; DOUBLED slopes (smc2_* chain, written by calc_step) instead of stepping
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

; adv_edges1 : advance both edge accumulators by the 1x steps (reading the SMC
;   operands as data). Shared by the h==0 segment path and the paired loop's
;   odd-entry / odd-exit single rows. Clobbers A only.
;   Relocated to the free $0F80 gap (game_text code ends ~$0B24, text data
;   starts $1000) -- the $2000-$3FFF chain is nearly full; `org` resumes after.
adv_edges1_resume equ *
        org $0F80
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
        ert *>$0FFF                 ; stay clear of the $1000 text-data segment
        org adv_edges1_resume       ; back to the $2000 chain

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

; emit_span : a_lo:a_hi .. b_lo:b_hi (320-space, clipped) -> page span.
;   slen = byte_b - byte_a = WIDTH-1, exactly what the blitter BCB wants --
;   so no +1 here and no -1 in fill_span (saves both on every span).
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
