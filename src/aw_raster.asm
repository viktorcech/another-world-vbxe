;=============================================================================
; Integer 16.16 raster (mirror of fill_poly_int / _slope in tools/aw_sim.py)
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
.if 1
        ; (skill pass 2026-09-09, same as the game fork: indices composed ONCE -- i=1,
        ;  j=n-2 -- X = n-1 indexes cr straight from nverts. Same values, same reads.)
        lda #1
        sta i_idx                   ; i = 1 (the first segment is pts[0] -> pts[1])
        ldx nverts
        stx numv
        dex                         ; X = n-1 = j
        lda #0                      ; per-polygon half-res row parity (relative to THIS poly's
.ifdef HIRES_CAP
        sta rpar                    ;   top, not absolute hy) -> every poly draws its 1st row as
.endif                             ;   a 2-tall span, so small polys are never dropped in half
                                   ;   mode (was: absolute-even -> 1px rocks on odd y vanished)
        sta cr0                     ; cr = (pts_x[j] + $8000) << 16  (X biased to
        sta cr1                     ;   unsigned: +$8000 = +$80 in the integer hi byte,
        sta cl0                     ;   so the edge compare/clip can be unsigned)
        sta cl1                     ; cl = (pts_x[0] + $8000) << 16
        lda pts_xlo,x
        sta cr2
        lda pts_xhi,x
        clc
        adc #$80
        sta cr3
        dex
        stx j_idx                   ; j = n-2
        lda pts_ylo                 ; hy = pts_y[0]
        sta hy_lo
        lda pts_yhi
        sta hy_hi
        lda pts_xlo                 ; cl = pts_x[0] (+bias)
        sta cl2
        lda pts_xhi
        clc
        adc #$80
        sta cl3
?seg    lda numv
        sec
        sbc #2
        sta numv
        bne ?cont
        rts
?cont
        ; (skill pass 2026-09-09 -- "the value lives in A": each 16-bit difference is
        ;  composed in the accumulator straight from the point arrays and stored ONCE,
        ;  dvr/dvl go DIRECTLY into calc_step's dv input, and calc_step now writes the
        ;  step bytes straight into the ?row SMC operands (X = 0 / SMC_LD selects the
        ;  chain) instead of staging them in N0..N3 for an 8-instruction copy here.
        ;  i >= 1 and j+1 <= n-1 for every valid (even-n) AW polygon, so the -1/+1
        ;  offsets never leave the arrays. Same values, same order, same wrap.)
        ; h = pts_y[i] - pts_y[i-1]
        ldx i_idx
        lda pts_ylo,x
        sec
        sbc pts_ylo-1,x
        sta hgt_lo
        lda pts_yhi,x
        sbc pts_yhi-1,x
        sta hgt_hi
        ; hh = (h>0) ? h : 1   (h >= 256 keeps the old truncation: hh = h & $FF)
        jmi ?hh1                    ; N from the hi byte just stored (?hh1 is out of
        ora hgt_lo                  ;   line past the row loop -> MADS Jcc pseudo-ops)
        jeq ?hh1
        lda hgt_lo
        sta hh
?slopes
        ; dv = dvr = pts_x[j] - pts_x[j+1]
        ldx j_idx
        lda pts_xlo,x
        sec
        sbc pts_xlo+1,x
        sta dv_lo
        lda pts_xhi,x
        sbc pts_xhi+1,x
        sta dv_hi
        ldx #0                      ; step_r -> calc_step writes the smc_cr* operands
        jsr calc_step
        ; dv = dvl = pts_x[i] - pts_x[i-1]  (calc_step touches neither pts nor i_idx)
        ldx i_idx
        lda pts_xlo,x
        sec
        sbc pts_xlo-1,x
        sta dv_lo
        lda pts_xhi,x
        sbc pts_xhi-1,x
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
        lda hgt_hi                  ; h < 0 : skip (edges untouched)
        jmi ?segnext                ;   (Jcc: ?segnext sits past the row loop)
        ora hgt_lo                  ; h == 0 : advance edges once, no draw
        bne ?hnz                    ;   (h >= 256 with lo = 0 still draws: A = hi != 0)
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
?hnz    lda hgt_lo
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
?hh1    lda #1                      ; h <= 0 : hh = 1 (divisor floor) -- out of line so the
        sta hh                      ;   common h > 0 segment pays no jmp
        jmp ?slopes
.endp

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
        jne ?ret                    ; a_hi > $81 -> a >= $8200 -> skip  (Jcc pseudo-ops:
        lda a_lo                    ;   MADS emits a branch when ?ret is in range, the
        cmp #$40                    ;   old ?bail trampoline otherwise -- never worse)
        jcs ?ret                    ; a >= $8140 (real >= 320) -> skip
?aok    lda b_hi                    ; if b < $8000 (b_real < 0) skip
        cmp #$80
        jcc ?ret
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
?ret    rts
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
