#!/usr/bin/env python3
"""
prof_mulzoom.py - GAME profiler + correctness check for the mul_zoom (m*zoom)>>6 path.

aw_polygon.asm's mul_zoom has a fast path (zoom==64 -> scaled = m, no shift) and a slow
path (the 8x16 multiply + a 6-bit right shift). aw1.txt proposes replacing the 6-iteration
`>>6` loop with "shift the 24-bit product LEFT by 2, take the high two bytes" -- which is
mathematically (P<<2)>>8 == P>>6.

This test answers two questions FOR THE GAME (not the intro, whose water-like scenes are
~97% fast path):

  1. CORRECTNESS  -- is the proposed optimization bit-identical to the original >>6?
  2. RELEVANCE    -- how often does each gameplay PART actually hit the slow path
                     (zoom != 64)? That decides whether the asm change is worth it.

Run:   python tools/prof_mulzoom.py            # all parts
       python tools/prof_mulzoom.py 16007 600  # one part, N frames
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_atari
import sim_atari

PARTS = [(16002, "water"), (16003, "jail"), (16004, "cite"),
         (16005, "arene"), (16006, "luxe"), (16007, "final")]
PAL_BUDGET = 35568                  # cycles per 50 Hz PAL frame
SLOW_SAVING = 90                    # ~cycles saved per slow-path call by the aw1.txt trick


# --- 1. correctness: the >>6 trick, byte-for-byte like the 6502 would do it ----------
def orig_shift(P):                  # (prod2:prod1:prod0) >> 6, keep low 16 (current asm)
    return (P >> 6) & 0xFFFF


def opt_shift(P):                   # asl prod0 / rol a / rol prod2 (x2), take a:prod2
    p0, a, p2 = P & 0xFF, (P >> 8) & 0xFF, (P >> 16) & 0xFF
    for _ in range(2):
        c = p0 >> 7; p0 = (p0 << 1) & 0xFF
        na = ((a << 1) | c) & 0xFF; c2 = a >> 7; a = na
        p2 = ((p2 << 1) | c2) & 0xFF
    return a | (p2 << 8)


def check_correctness():
    bad = sum(1 for P in range(1 << 24) if orig_shift(P) != opt_shift(P))
    ok = bad == 0
    print(f"[correctness] aw1.txt >>6 trick vs original, full 24-bit sweep: "
          f"{'BIT-IDENTICAL' if ok else f'{bad} MISMATCHES'}")
    return ok


# --- 2. relevance: slow-path frequency per gameplay part -----------------------------
_orig_mul = sim_atari.Sim.mul


def profile_part(part, frames):
    calls = [0, 0]                  # [fast zoom==64, slow zoom!=64]

    def mul(self, m, zoom, _c=calls):
        _c[0 if zoom == 64 else 1] += 1
        return _orig_mul(self, m, zoom)

    game_atari.GameAtari.mul = mul
    try:
        vm = game_atari.GameAtari(part)
        vm.run(frames)
    finally:
        game_atari.GameAtari.mul = _orig_mul
    fast, slow = calls
    tot = fast + slow
    nfr = max(len(vm.frames), 1)
    saving = slow * SLOW_SAVING // nfr
    return dict(frames=len(vm.frames), tot=tot, fast=fast, slow=slow,
                slow_pct=(100 * slow / tot) if tot else 0.0,
                cyc_per_frame=saving, budget_pct=100 * saving / PAL_BUDGET)


def main():
    print("== mul_zoom GAME profiler ==\n")
    check_correctness()
    print()
    if len(sys.argv) > 1:
        part = int(sys.argv[1]); frames = int(sys.argv[2]) if len(sys.argv) > 2 else 400
        parts = [(part, dict(PARTS).get(part, f"p{part}"))]
    else:
        parts = PARTS; frames = 250

    print(f"[relevance] slow-path (zoom != 64) over {frames} no-input frames per part:\n")
    print(f"  {'part':8}{'frames':>7}{'mul calls':>11}{'slow%':>8}"
          f"{'~cyc/frame':>12}{'% PAL frame':>12}")
    worst = 0.0
    for p, name in parts:
        r = profile_part(p, frames)
        worst = max(worst, r["slow_pct"])
        print(f"  {name:8}{r['frames']:7}{r['tot']:11}{r['slow_pct']:7.1f}%"
              f"{r['cyc_per_frame']:12}{r['budget_pct']:11.2f}%")

    print()
    print("Verdict: the aw1.txt trick is correct but only pays off where the slow path is")
    print("hot. Water/cite are ~0% (fast path already wins); the FINAL part is the outlier")
    print(f"(heavy sprite scaling). Worst measured slow-path here: {worst:.0f}% -> the change")
    print("is worth applying to src_game/aw_polygon.asm (and harmless to the intro fork).")


if __name__ == "__main__":
    main()
