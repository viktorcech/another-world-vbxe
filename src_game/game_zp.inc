;=============================================================================
; game_zp.inc  -  GAME-ONLY zero-page placement of the polygon decoder locals.
;
;   aw4.txt (ZP hygiene): the decode hot loops touch scaled_lo/hi, x0, y0, vidx
;   per vertex/child. In src/aw_equates.inc those live in absolute RAM (RAMB+53..),
;   so every lda/sta/adc/inc on them costs +1 cyc vs zero page. tools/prof_zphygiene.py
;   measured the win: ~5% of a PAL frame on light parts, but ~70% on arene (heavy
;   sprite scaling) -- exactly the scenes that drop frames. Output-identical (pure
;   addressing-mode change).
;
;   The INTRO (src/) is frozen, so we do NOT edit the shared aw_equates.inc. Instead
;   the GAME fork (src_game/aw_polygon.asm) refers to g_* symbols defined HERE, mapped
;   into zero page. The shared scaled_lo/x0/y0/vidx stay in RAM for the intro build.
;
;   No free ZP gap is left in the GAME (game_vm.asm took $B8-$BD for VM scratch), so
;   we UNION onto the raster accumulators $C0-$C6 (cr0..cl2). This is safe in TIME:
;   the decoder finishes building the point list (these vars) BEFORE fill_poly_int
;   walks it, and fill_poly_int reinitialises cr/cl at entry -- so the decode locals
;   and the raster accumulators never hold live values simultaneously.  (aw4.txt union.)
;
;   Include AFTER src/aw_equates.inc (which defines cr0..cl2) and BEFORE
;   src_game/aw_polygon.asm.
;=============================================================================
g_scaled_lo equ cr0                 ; $C0   (mul_zoom output lo)
g_scaled_hi equ cr1                 ; $C1   (mul_zoom output hi)
g_x0        equ cr2                 ; $C2-$C3 (do_fill bbox origin x)
g_y0        equ cl0                 ; $C4-$C5 (do_fill bbox origin y)
g_vidx      equ cl2                 ; $C6   (do_fill vertex index)
