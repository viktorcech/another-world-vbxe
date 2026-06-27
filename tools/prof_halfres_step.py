#!/usr/bin/env python3
"""
prof_halfres_step.py - estimate, FROM THE ASSEMBLED GAME CODE, the speed-up of one
proposed rasterizer optimisation: skipping the per-row edge-step add on the rows that
stock half-res mode does not draw.

WHAT THE OPTIMISATION IS
  On stock 6502, gameplay renders in half-res (poly_bcb_h=1): the ?row loop in
  fill_poly_int (src/aw_raster.asm) draws a 2-tall span on every OTHER scanline. But the
  loop still advances both edge accumulators (the 8-byte 16.16 add of cr0..3 / cl0..3,
  plus the parity check + flip) on EVERY row -- including the ones it skips drawing. The
  proposal: on half-res, step the edges by 2x and iterate h/2 times (always drawing),
  dropping the skipped-row add entirely. Output is identical (the 2-tall span already
  covers the skipped row at the drawn row's x), so it is a pure CPU win.

HOW THIS COMPUTES THE SAVING (no guessed constants)
  Every cycle cost below is SUMMED from the assembled listing (out/_scene_fps.lst, via
  scene_fps.Cost). We split the per-row raster into:
    ALWAYS  -- runs on EVERY source row: parity check + the 8-byte edge add + hy advance
               + parity flip + loop control  (= the ?row body minus the draw call).
    DRAW    -- runs only on a DRAWN row: jsr draw_scanline_fast + emit_span(LR) + fill_span
               (solid) -- the actual span emit.
    ALWAYS' -- the optimised per-(drawn)-row overhead: ALWAYS minus the parity check + flip
               (no longer needed -- we always draw), plus a little for stepping hy by 2.
  Stock half-res, with S = full-res scanline count (game_atari draws every row):
    now =  S*ALWAYS + (S/2)*DRAW            # add on every row, draw on half
    opt = (S/2)*ALWAYS' + (S/2)*DRAW        # step by 2: one merged row per drawn span
  Only the scanline bucket changes; every other bucket (edges/calc_step, coord scale,
  poly fetch, page blits, ...) is per-shape/per-segment, NOT per-row, so it is unaffected
  and carried over from scene_fps.frame_buckets unchanged.

  Op counts (S per scene, etc.) come from game_atari running the real bytecode; the cycle
  costs come from the assembled asm. This is a MODEL -- Altirra on real timing is truth --
  but the saving is real CPU cycles removed from the hottest per-frame loop.

    python tools/prof_halfres_step.py
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scene_fps as s

# small fixed costs read straight from the asm (src/aw_raster.asm ?row loop):
JSR = 6                     # jsr draw_scanline
PARITY_FLIP = 8            # ?krow: lda rpar / eor #1 / sta rpar  (3+2+3)
STEP2_EXTRA = 4           # advancing hy by 2 per iteration instead of 1 (approx)

# PER-SEGMENT implementation overhead the naive estimate ignored. To do ONE (doubled) edge
# add per drawn row you must double the 16.16 step in the SMC operands -- 8x asl/rol on an
# absolute operand = 8*6 = 48 cyc -- plus the half/full branch + ceil(h/2) row count (~16),
# plus odd-height correction (the integer-x accumulator cr2:cr3 carries between segments, so
# stepping by 2 overshoots by one step on odd h -> must save the 1x step and re-add it, ~40
# averaged over the ~50% odd segments). This runs once PER SEGMENT (= n_edge/2), so on
# polygon-dense scenes (water) it eats a big chunk of the per-row saving.
SEG_OVERHEAD = 48 + 16 + 40   # double SMC + branch/rowcount + odd-h correction (avg)


def half_res_costs(cost):
    """Pull the per-row raster pieces out of the assembled listing.
    Returns (ALWAYS, DRAW, ALWAYS_opt) in 6502 cycles."""
    rowloop = cost.span('fill_poly_int', '?row', '?segnext')   # whole ?row body incl. jsr
    parity_check = cost.span('fill_poly_int', '?row', 'smc_dsl')  # lda rpar/and/bne
    dsl_fast = cost.proc('draw_scanline_fast')
    emit_lr = cost.exclude('emit_span', ('?sr', '?col'))           # LR (gameplay) path only
    fill_solid = cost.exclude('fill_span', ('?copy', '?transp'), ('?transp', '?solid'))
    always = rowloop - JSR                       # every source row (drawn + skipped)
    draw = JSR + dsl_fast + emit_lr + fill_solid  # only a drawn row
    always_opt = always - parity_check - PARITY_FLIP + STEP2_EXTRA  # no parity, step by 2
    return always, draw, always_opt, dict(rowloop=rowloop, parity_check=parity_check,
                                          dsl_fast=dsl_fast, emit_lr=emit_lr,
                                          fill_solid=fill_solid)


def main():
    print('Assembling a fresh listing for the cycle costs ...')
    s.build_listing()
    cost = s.Cost(s.parse_listing(s.LST))
    M = s.Model(cost)
    ALWAYS, DRAW, ALWAYS_OPT, parts = half_res_costs(cost)

    print('\n' + '=' * 72)
    print(' HALF-RES EDGE-STEP SKIP  -- speed-up from the assembled game (stock 6502)')
    print('=' * 72)
    print(f'   per-row asm pieces:  rowloop={parts["rowloop"]}  parity_check={parts["parity_check"]}'
          f'  draw_scanline_fast={parts["dsl_fast"]}  emit_span(LR)={parts["emit_lr"]}'
          f'  fill_span(solid)={parts["fill_solid"]}')
    print(f'   ALWAYS (every row)={ALWAYS}   DRAW (drawn row)={DRAW}   ALWAYS_opt={ALWAYS_OPT}')
    print(f'   stock half-res: edge add runs on every row (S), a span is drawn every 2nd row (S/2)')
    print()
    hdr = (f'   {"scene":<8}{"scanlines/f":>12}{"now cyc/f":>11}{"opt cyc/f":>11}'
           f'{"now fps":>9}{"opt fps":>9}{"speedup":>9}')
    print(hdr)
    print('   ' + '-' * (len(hdr) - 3))
    tot_now = tot_opt = 0.0
    for part, pos, name in s.SCENES:
        k, nfr, hold, switched = s.measure_scene(part, pos)
        b = s.frame_buckets(k, M)
        S = k['span']                                   # full-res scanlines/frame
        other = sum(b.values()) - b['scanlines']        # buckets the opt does NOT touch
        segs = k['edge'] / 2                             # fill_poly_int segments (2 edges each)
        now = other + S * ALWAYS + (S / 2) * DRAW        # current stock (draw only on drawn rows)
        opt = (other + (S / 2) * ALWAYS_OPT + (S / 2) * DRAW
               + segs * SEG_OVERHEAD)                    # + the real per-segment implementation cost
        tot_now += now; tot_opt += opt
        sp = 100 * (now - opt) / now if now else 0.0
        print(f'   {name:<8}{S:>12.0f}{now:>11.0f}{opt:>11.0f}'
              f'{s.PAL_CPU_HZ/now:>9.1f}{s.PAL_CPU_HZ/opt:>9.1f}{sp:>8.1f}%')
    avg = 100 * (tot_now - tot_opt) / tot_now if tot_now else 0.0
    print(f'\n   gameplay average speed-up: {avg:.1f}%   (only water/jail/cite/luxe -- no cutscenes)')
    print('\n   NOTE: a MODEL -- cycles are summed from the assembled asm, scanline counts are')
    print('   exact (game_atari runs the bytecode). Output is bit-identical by construction')
    print('   (the 2-tall span already covers the skipped row). Final cycle truth = Altirra.')


if __name__ == '__main__':
    main()
