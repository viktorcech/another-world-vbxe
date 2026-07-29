#!/usr/bin/env python3
"""verify_halfstep.py - equivalence proof for the fps-wave-2 half-res raster loop.

THE CLAIM: in half-vertical-res mode (poly_bcb_h=1, stock 6502) the new PAIRED
row loop in src_game/aw_raster.asm -- draw the parity-0 row, then advance the
16.16 edge accumulators ONCE with DOUBLED slopes -- draws the polygon spans at
BIT-IDENTICAL edge positions to the old loop (step 1x every row, parity-test
every row), and leaves bit-identical cross-segment state (cr, cl, hy, rpar).

Model: exact 32-bit wrap-around accumulators (as the 6502 4-byte adc chains),
16-bit signed hy, the poly-relative parity bit rpar carried ACROSS segments.
step2 = (step << 1) & 0xFFFFFFFF exactly as calc_step's asl/rol doubling.

Divergence policy (matches the asm): the old loop tests the y-bound after EVERY
row and exits the whole shape once hy >= 200; the new loop tests once per pair
(and after the odd-entry normalization row), so it may run a little PAST the
point where the old loop bailed. hy is monotonic non-decreasing (h<0 segments
don't move it), so every span either loop would emit beyond that point has
hy >= 200 and is discarded by the draw_scanline y-guard -- the GUARDED span
sets must still be identical, which is what this tool asserts.

Run:  python tools/verify_halfstep.py         (exits 0 = proof holds)
"""
import random
import sys

M32 = 0xFFFFFFFF
SCRH = 200


def old_segments(cr, cl, hy, rpar, segs):
    """The OLD loop over a whole shape: segs = [(h, stepr, stepl), ...].
    Returns (spans, state, exited). Spans = (hy, cr, cl) at each DRAWN row
    (parity 0), pre-filtered by nothing -- caller applies the y-guard."""
    spans = []
    for h, sr, sl in segs:
        if h == 0:
            cr = (cr + sr) & M32
            cl = (cl + sl) & M32
            continue
        if h < 0:
            continue
        row_cnt = h
        while True:
            if rpar == 0:
                spans.append((hy, cr, cl))
            cr = (cr + sr) & M32
            cl = (cl + sl) & M32
            hy += 1
            if hy >= 0 and hy >= SCRH:      # yk_tst: bmi keep / >=200 exit
                return spans, (cr, cl, hy, rpar), True
            rpar ^= 1
            row_cnt -= 1
            if row_cnt == 0:
                break
    return spans, (cr, cl, hy, rpar), False


def new_segments(cr, cl, hy, rpar, segs):
    """The NEW paired loop (asm ?rowh mirror). step2 = (step<<1) & M32."""
    spans = []
    for h, sr, sl in segs:
        sr2 = (sr << 1) & M32
        sl2 = (sl << 1) & M32
        if h == 0:
            cr = (cr + sr) & M32
            cl = (cl + sl) & M32
            continue
        if h < 0:
            continue
        row_cnt = h
        if rpar == 1:                       # odd entry: row not drawn, re-align
            cr = (cr + sr) & M32
            cl = (cl + sl) & M32
            hy += 1
            rpar = 0
            row_cnt -= 1
            if row_cnt == 0:
                continue                    # segment consumed (no bound test = CONT)
            if hy >= 0 and hy >= SCRH:
                return spans, (cr, cl, hy, rpar), True
        while True:
            if row_cnt >= 2:
                spans.append((hy, cr, cl))  # parity-0 row, guarded by the caller
                cr = (cr + sr2) & M32
                cl = (cl + sl2) & M32
                hy += 2
                row_cnt -= 2
                if row_cnt == 0:
                    break                   # segment consumed exactly
                if hy >= 0 and hy >= SCRH:
                    return spans, (cr, cl, hy, rpar), True
            else:                           # exactly 1 row left, parity 0
                spans.append((hy, cr, cl))
                cr = (cr + sr) & M32
                cl = (cl + sl) & M32
                hy += 1
                rpar = 1
                break
    return spans, (cr, cl, hy, rpar), False


def guard(spans):
    """draw_scanline's y-guard: only rows 0..199 reach fill_span."""
    return [s for s in spans if 0 <= s[0] < SCRH]


def run_case(cr, cl, hy, rpar, segs, onscreen):
    so, sto, exo = old_segments(cr, cl, hy, rpar, segs)
    sn, stn, exn = new_segments(cr, cl, hy, rpar, segs)
    if onscreen:
        # fast/yok dispatch: hy provably stays < 200 -> neither may exit,
        # spans must match RAW and end state must match exactly.
        assert not exo and not exn, "on-screen shape must never y-exit"
        assert so == sn, f"raw span mismatch: {so[:4]} vs {sn[:4]}"
        assert sto == stn, f"state mismatch: {sto} vs {stn}"
    else:
        # clip dispatch: compare the GUARDED spans; if the old loop exited,
        # the new loop may emit extra spans but ALL of them must be y-culled.
        go, gn = guard(so), guard(sn)
        assert go == gn, f"guarded span mismatch:\n old={go[:6]}\n new={gn[:6]}"
        if not exo and not exn:
            assert sto == stn, f"state mismatch (no exit): {sto} vs {stn}"


def main():
    rng = random.Random(0xA8)
    cases = 0

    # --- exhaustive small cases: every (rpar, h1, h2) with tiny slopes -----
    for rpar in (0, 1):
        for h1 in range(0, 9):
            for h2 in range(0, 9):
                for sr in (0, 1, 0x10000, M32 & -0x10000, 0x1234567):
                    segs = [(h1, sr, (sr * 3) & M32), (h2, (sr * 5) & M32, sr)]
                    run_case(0x80000000, 0x80100000, 10, rpar, segs, True)
                    cases += 1

    # --- randomized on-screen shapes (fast/yok class: hy never nears 200) --
    for _ in range(4000):
        nseg = rng.randint(1, 6)
        hy = rng.randint(0, 60)
        left = 190 - hy                     # keep the total under the bottom edge
        segs = []
        for _ in range(nseg):
            h = rng.choice([0, 0, rng.randint(1, max(1, min(24, left)))])
            left -= h
            sr = rng.getrandbits(32)
            sl = rng.getrandbits(32)
            segs.append((h, sr, sl))
        run_case(rng.getrandbits(32), rng.getrandbits(32), hy,
                 rng.randint(0, 1), segs, True)
        cases += 1

    # --- randomized CLIPPED shapes (hy may start <0 and/or run past 200) ---
    for _ in range(4000):
        nseg = rng.randint(1, 6)
        hy = rng.randint(-40, 199)
        segs = []
        for _ in range(nseg):
            h = rng.choice([0, rng.randint(1, 80), -rng.randint(1, 5)])
            sr = rng.getrandbits(32)
            sl = rng.getrandbits(32)
            segs.append((h, sr, sl))
        run_case(rng.getrandbits(32), rng.getrandbits(32), hy,
                 rng.randint(0, 1), segs, False)
        cases += 1

    print(f"OK: {cases} cases -- paired half-res loop is span- and "
          f"state-identical to the 1x loop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
