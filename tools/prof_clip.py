#!/usr/bin/env python3
"""
prof_clip.py - GAME: how often is the per-scanline horizontal clip in draw_scanline redundant?

draw_scanline runs EVERY scanline of EVERY polygon: order (xl,xr)->(a,b), then bounds-check
and clamp a,b to [0,319]. For a polygon that sits fully on-screen the clamp is a no-op done
hundreds of times. A per-SEGMENT fast path -- "if this segment's edges never leave [0,319],
draw its scanlines with no clip" -- skips that work. It is OUTPUT-IDENTICAL: clamping a value
already in range changes nothing (the user's hard constraint: graphics must not break).

This measures the opportunity WITHOUT touching asm: it mirrors the exact 16.16 edge walk and
classifies, per drawn scanline, whether the horizontal clip actually does anything, and whether
xl<=xr held (so the min/max swap could also be hoisted). Per SEGMENT it reports how many are
entirely clip-free / swap-free, since the real fast path branches once per segment, not per row.

  python tools/prof_clip.py            # all parts, 250 frames
  python tools/prof_clip.py 16005 400  # one part
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
CYC_CLIP   = 25     # draw_scanline bounds-check + clamp branches skipped on a clip-free segment
CYC_SWAP   = 12     # the xl<=xr order block, skippable when a segment never swaps

_c = {}


def _reset():
    _c.clear()
    _c.update(scan=0, clipfree=0, noswap=0, segs=0, seg_clipfree=0, seg_noswap=0,
              seg_scan_clipfree=0, seg_scan_noswap=0)


def fill_poly_int_probe(self, pts, color):
    n = len(pts)
    if n < 3:
        return
    i = 0; j = n - 1
    hy = pts[0][1]
    cr = (pts[j][0] & 0xFFFF) << 16
    cl = (pts[i][0] & 0xFFFF) << 16
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
        sr = self.calc_step(dvr, hh)
        sl = self.calc_step(dvl, hh)
        i += 1; j -= 1
        cr = (cr & 0xFFFF0000) | 0x7FFF
        cl = (cl & 0xFFFF0000) | 0x8000
        if h == 0:
            cr = (cr + sr) & 0xFFFFFFFF
            cl = (cl + sl) & 0xFFFFFFFF
            continue
        if h < 0:
            continue
        # one segment: walk its h scanlines, classify each on-screen row
        _c["segs"] += 1
        seg_scan = 0; seg_clipfree = True; seg_noswap = True
        for _ in range(h & 0xFF):
            if 0 <= hy < H:
                xr = s16((cr >> 16) & 0xFFFF)
                xl = s16((cl >> 16) & 0xFFFF)
                noswap = xl <= xr
                a, b = (xl, xr) if noswap else (xr, xl)
                if a <= W320 - 1 and b >= 0:          # span at least partly on-screen
                    seg_scan += 1
                    _c["scan"] += 1
                    clipfree = (a >= 0) and (b <= W320 - 1)
                    if clipfree:
                        _c["clipfree"] += 1
                    else:
                        seg_clipfree = False
                    if noswap:
                        _c["noswap"] += 1
                    else:
                        seg_noswap = False
            cr = (cr + sr) & 0xFFFFFFFF
            cl = (cl + sl) & 0xFFFFFFFF
            hy += 1
        if seg_scan:
            if seg_clipfree:
                _c["seg_clipfree"] += 1
                _c["seg_scan_clipfree"] += seg_scan
            if seg_noswap:
                _c["seg_noswap"] += 1
                _c["seg_scan_noswap"] += seg_scan


def profile_part(part, frames):
    _reset()
    game_atari.GameAtari.fill_poly_int = fill_poly_int_probe
    try:
        vm = game_atari.GameAtari(part)
        vm.run(frames)
    finally:
        game_atari.GameAtari.fill_poly_int = sim_atari.Sim.fill_poly_int
    nfr = max(len(vm.frames), 1)
    scan = max(_c["scan"], 1)
    # cycles saved by a per-segment fast path: only segments that are ENTIRELY clip-free
    # (resp. swap-free) get to skip the work, on every one of their scanlines.
    cyc = (_c["seg_scan_clipfree"] * CYC_CLIP + _c["seg_scan_noswap"] * CYC_SWAP) / nfr
    return dict(frames=len(vm.frames), scan=_c["scan"],
                clipfree_pct=100 * _c["clipfree"] / scan,
                noswap_pct=100 * _c["noswap"] / scan,
                seg_cf_pct=100 * _c["seg_clipfree"] / max(_c["segs"], 1),
                seg_scan_cf_pct=100 * _c["seg_scan_clipfree"] / scan,
                cyc=cyc, pct=100 * cyc / PAL_BUDGET)


def main():
    if len(sys.argv) > 1:
        part = int(sys.argv[1]); frames = int(sys.argv[2]) if len(sys.argv) > 2 else 400
        parts = [(part, dict(PARTS).get(part, f"p{part}"))]
    else:
        parts = PARTS; frames = 250

    print("== draw_scanline clip redundancy (GAME, output-identical fast-path opportunity) ==\n")
    print(f"{frames} no-input frames/part. 'clip-free' = span already in [0,319] (clamp is a no-op).\n")
    print(f"  {'part':7}{'scanlines':>10}{'clip-free':>11}{'no-swap':>9}"
          f"{'seg clipfree':>14}{'rows in cf-seg':>15}{'~cyc/frame':>12}{'% PAL':>8}")
    for p, name in parts:
        r = profile_part(p, frames)
        print(f"  {name:7}{r['scan']:10}{r['clipfree_pct']:10.1f}%{r['noswap_pct']:8.1f}%"
              f"{r['seg_cf_pct']:13.1f}%{r['seg_scan_cf_pct']:14.1f}%{r['cyc']:12.0f}{r['pct']:7.2f}%")

    print()
    print("Reading it: 'clip-free' is the ceiling (per-row); 'rows in cf-seg' is what a per-SEGMENT")
    print("branch actually captures (a segment qualifies only if ALL its rows are in-bounds). The")
    print("gap between them is segments that cross a screen edge -- they still pay full clip.")
    print("If 'rows in cf-seg' is high, an output-identical no-clip segment path is worth writing;")
    print("the swap-free column says whether the xl/xr min/max can be hoisted in the same branch.")


if __name__ == "__main__":
    main()
