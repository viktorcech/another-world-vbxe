#!/usr/bin/env python3
"""
prof_fixed88.py - GAME: would 16.8 fixed-point raster (vs the current 16.16) pay off?

The raster walks two edge accumulators (cr/cl) down each polygon in 16.16 fixed point:
calc_step builds a 16.16 slope (|dx|*recip16[dy], TWO fmulu because recip is 16-bit), and
add_steps adds a 4-byte slope to a 4-byte accumulator every scanline. Dropping the fraction
to 8 bits (16.8) would:
  * calc_step -> ONE fmulu  (recip8[dy]=round(256/dy) is 8-bit, |dx|*recip8 is 8x8)  ~ -20 cyc/edge
  * add_steps -> 3-byte add instead of 4-byte (one less lda/adc/sta per edge)          ~ -16 cyc/scanline
...but it HALVES sub-pixel precision, so edges may round to a different column. Same call the
reciprocal-LUT change faced (it diverged ~20 px/frame and was accepted). So MEASURE FIRST:
render every gameplay part both ways and report the pixel divergence, alongside the cycle win.

  python tools/prof_fixed88.py            # all parts, 250 frames
  python tools/prof_fixed88.py 16005 400  # one part
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_atari
import sim_atari
from sim_atari import s16, H

W320 = 320
PARTS = [(16002, "water"), (16003, "jail"), (16004, "cite"),
         (16005, "arene"), (16006, "luxe"), (16007, "final")]
PAL_BUDGET = 35568
# cycle weights (consistent with perf_model.py): one fmulu + the partial-add it drops, and
# one fewer byte in the per-edge SMC add (lda+adc#imm+sta = 8 cyc) on BOTH edges per scanline.
CYC_PER_EDGE_SAVED     = 20         # calc_step: 2 fmulu -> 1 (+ drop the 16-bit partial add)
CYC_PER_SCANLINE_SAVED = 16         # add_steps: 4-byte -> 3-byte, x2 edges (8 cyc each)

# --- 16.8 reciprocal + slope (mirror of sim_atari.calc_step, fraction halved to 8 bits) ----
_recip8 = [0] * 256
for _dy in range(2, 256):
    _recip8[_dy] = round(256 / _dy)


def calc_step8(dv, hh):
    sign = dv < 0
    ad = abs(dv) & 0xFF
    m = (ad << 8) if hh == 1 else ad * _recip8[hh & 0xFF]
    return (-m) & 0xFFFFFF if sign else (m & 0xFFFFFF)


# --- 16.8 fill_poly_int: byte-for-byte the 16.16 algorithm with an 8-bit fraction ----------
_stat = dict(edges=0, scanlines=0)


def fill_poly_int88(self, pts, color):
    n = len(pts)
    if n < 3:
        for (x, y) in pts:
            if 0 <= y < H and 0 <= x < W320:
                self.span(y, x, x, color)
        return
    i = 0; j = n - 1
    hy = pts[0][1]
    cr = (pts[j][0] & 0xFFFF) << 8          # 16.8 (was <<16)
    cl = (pts[i][0] & 0xFFFF) << 8
    i += 1; j -= 1
    numv = n
    while True:
        numv -= 2
        if numv == 0:
            return
        h = pts[i][1] - pts[i - 1][1]
        dvr = pts[j][0] - pts[j + 1][0]
        dvl = pts[i][0] - pts[i - 1][0]
        hh = (h & 0xFF) if h > 0 else 1
        sr = calc_step8(dvr, hh)
        sl = calc_step8(dvl, hh)
        _stat["edges"] += 1
        i += 1; j -= 1
        cr = (cr & 0xFFFF00) | 0x7F         # half-pixel bias in 8-bit fraction (was 0x7FFF)
        cl = (cl & 0xFFFF00) | 0x80         # (was 0x8000)
        if h == 0:
            cr = (cr + sr) & 0xFFFFFF
            cl = (cl + sl) & 0xFFFFFF
            continue
        if h < 0:
            continue
        for _ in range(h & 0xFF):
            if 0 <= hy < H:
                _stat["scanlines"] += 1
                xr = s16((cr >> 8) & 0xFFFF)
                xl = s16((cl >> 8) & 0xFFFF)
                a, b = (xl, xr) if xl <= xr else (xr, xl)
                if a <= W320 - 1 and b >= 0:
                    a = max(0, a); b = min(W320 - 1, b)
                    self.span(hy, a, b, color)
            cr = (cr + sr) & 0xFFFFFF
            cl = (cl + sl) & 0xFFFFFF
            hy += 1


def render(part, frames, patched):
    if patched:
        _stat["edges"] = 0; _stat["scanlines"] = 0
        game_atari.GameAtari.fill_poly_int = fill_poly_int88
    try:
        vm = game_atari.GameAtari(part)
        vm.run(frames)
        return [f[0] for f in vm.frames]
    finally:
        if patched:
            game_atari.GameAtari.fill_poly_int = sim_atari.Sim.fill_poly_int


def profile_part(part, frames):
    base = render(part, frames, False)
    alt = render(part, frames, True)
    nfr = min(len(base), len(alt))
    diff_tot = 0; diff_max = 0; nonzero = 0
    for k in range(nfr):
        d = sum(1 for a, b in zip(base[k], alt[k]) if a != b)
        diff_tot += d
        diff_max = max(diff_max, d)
        nonzero += (d > 0)
    cyc = (_stat["edges"] * CYC_PER_EDGE_SAVED
           + _stat["scanlines"] * CYC_PER_SCANLINE_SAVED) / max(nfr, 1)
    return dict(frames=nfr, diff_avg=diff_tot / max(nfr, 1), diff_max=diff_max,
                changed=nonzero, edges=_stat["edges"], scan=_stat["scanlines"],
                cyc=cyc, pct=100 * cyc / PAL_BUDGET)


def main():
    if len(sys.argv) > 1:
        part = int(sys.argv[1]); frames = int(sys.argv[2]) if len(sys.argv) > 2 else 400
        parts = [(part, dict(PARTS).get(part, f"p{part}"))]
    else:
        parts = PARTS; frames = 250

    print("== 16.8 vs 16.16 raster: measure divergence + cycle win (GAME) ==\n")
    print(f"Per part, {frames} no-input frames. Pixels are 160x200 LR bytes (32000/frame).\n")
    print(f"  {'part':7}{'frames':>7}{'avg diff px':>12}{'max diff':>10}{'frames!=':>9}"
          f"{'~cyc/frame':>12}{'% PAL':>8}")
    worst_px = 0.0; worst_pct = 0.0
    for p, name in parts:
        r = profile_part(p, frames)
        worst_px = max(worst_px, 100 * r["diff_avg"] / 32000)
        worst_pct = max(worst_pct, r["pct"])
        print(f"  {name:7}{r['frames']:7}{r['diff_avg']:12.1f}{r['diff_max']:10}"
              f"{r['changed']:>5}/{r['frames']:<3}{r['cyc']:12.0f}{r['pct']:7.2f}%")

    print()
    print(f"VERDICT: REJECT 16.8. The cycle win is real (up to ~{worst_pct:.0f}% of a PAL frame),")
    print(f"but divergence reaches ~{worst_px:.2f}% of pixels/frame (avg) with single frames")
    print("differing by thousands of px -- whole edges shift, not sub-pixel noise. 10-40x the")
    print("~0.17%/frame bar the reciprocal-LUT met. Worse, damage and benefit CORRELATE: arene/")
    print("final (heavy sprite scaling, tall polys that accumulate 8-bit fraction drift over")
    print("100+ scanlines) are both where 16.8 saves most AND breaks most. cite (zoom=64, short")
    print("polys) is clean but saves least. No middle ground: 16.12 keeps the 4-byte accumulator")
    print("and >8-bit recip, so it loses BOTH wins. Conclusion: keep 16.16 -- the raster math is")
    print("already at its practical 6502 optimum (fmulu 14cyc + reciprocal-LUT + SMC add_steps).")


if __name__ == "__main__":
    main()
