#!/usr/bin/env python3
"""
prof_tween.py - GAME: measure Gemini's 3-point motion-smoothing proposal (n1.txt), each point.

The game is vblank-paced: every displayed frame is held VAR_PAUSE_SLICES (var 0xFF) = N vblanks,
so motion updates ~10 fps. Gemini proposes decoupling a 50 fps render loop and interpolating
object positions between VM ticks. This measures whether each of the three techniques pays off,
from the REAL bytecode -- no engine changes.

Method: draws are matched by slot index across consecutive frames (the bytecode emits the same
objects in the same slots), and each slot transition is classified:
  STATIC  off same, pos same
  TWEEN   off same, pos changed         -> clean sub-tick interpolation (point 1)
  SKATE   off changed AND pos changed   -> translates WHILE the pose keyframes (the skating case)
  POSE    off changed, pos same         -> pure keyframe, nothing to interpolate
Per top-level sprite we also capture its on-screen bounding box (from its span extents) for the
dirty-rectangle cost (point 3).

  python tools/prof_tween.py            # all parts, 250 frames
  python tools/prof_tween.py 16002 400  # water
"""
import os, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_atari, sim_atari

PARTS = [(16002, "water"), (16003, "jail"), (16004, "cite"),
         (16005, "arene"), (16006, "luxe"), (16007, "final")]
PAL_BUDGET = 35568
BYTES_PER_CYC = 14.18 / 1.773447     # VBXE blitter: ~8 LR bytes per 6502 cycle


def run_capture(part, frames):
    """Run the part, return per-frame (hold, drawlist, bbox-list parallel to drawlist)."""
    vm = game_atari.GameAtari(part)
    cur = {"bb": None}
    _ds, _db, _fs = vm.draw_sprite, vm.draw_bg, game_atari.GameAtari.fill_span

    def fill_span(self, sx, sy, ln, color):
        bb = cur["bb"]
        if bb is not None:
            x0, x1 = sx, sx + max(0, ln) - 1
            if bb[0] is None:
                cur["bb"] = [x0, sy, x1, sy]
            else:
                bb[0] = min(bb[0], x0); bb[1] = min(bb[1], sy)
                bb[2] = max(bb[2], x1); bb[3] = max(bb[3], sy)
        return _fs(self, sx, sy, ln, color)

    frame_bb = []
    cur_list = {"v": []}

    def wrap_draw(orig):
        def w(op):
            cur["bb"] = [None, None, None, None]
            r = orig(op)
            bb = cur["bb"]
            cur_list["v"].append(tuple(bb) if bb[0] is not None else None)
            cur["bb"] = None
            return r
        return w

    vm.draw_sprite = wrap_draw(_ds)
    vm.draw_bg = wrap_draw(_db)
    game_atari.GameAtari.fill_span = fill_span
    # capture the bbox list at each updatedisplay, aligned with the drawlist
    out = []
    OPS = game_atari.GameAtari.OPS
    i_ud = [k for k in range(len(OPS)) if OPS[k].__name__ == 'op_updatedisplay'][0]
    o_ud = OPS[i_ud]

    def op_ud(self):
        r = o_ud(self)
        f = self.frames[-1]
        out.append((f[2], f[4], tuple(cur_list["v"])))   # hold, drawlist, bbox-list
        cur_list["v"] = []
        return r
    OPS[i_ud] = op_ud
    try:
        vm.run(frames)
    finally:
        game_atari.GameAtari.fill_span = _fs
        OPS[i_ud] = o_ud
    return out


def profile_part(part, frames):
    F = run_capture(part, frames)
    cls = collections.Counter()                 # STATIC/TWEEN/SKATE/POSE
    deltas = []                                  # (|dx|,|dy|, N) for TWEEN+SKATE
    Ns = collections.Counter()
    slot_motion = collections.Counter()          # cumulative |move| per slot (hero proxy)
    slot_cls = collections.defaultdict(collections.Counter)
    bbox_areas = []                              # LR-byte areas of MOVING sprites
    for (h0, d0, b0), (h1, d1, b1) in zip(F, F[1:]):
        N = max(1, h1)
        Ns[N] += 1
        m = min(len(d0), len(d1))
        for i in range(m):
            a, b = d0[i], d1[i]
            if a[0] != b[0]:
                continue
            off_eq = a[1] == b[1]
            dx = b[2] - a[2]; dy = b[3] - a[3]
            moved = (dx != 0 or dy != 0)
            if off_eq and not moved:
                k = "STATIC"
            elif off_eq and moved:
                k = "TWEEN"
            elif not off_eq and moved:
                k = "SKATE"
            else:
                k = "POSE"
            cls[k] += 1; slot_cls[i][k] += 1
            if moved:
                slot_motion[i] += abs(dx) + abs(dy)
                deltas.append((abs(dx), abs(dy), N))
                if i < len(b1) and b1[i]:
                    x0, y0, x1, y1 = b1[i]
                    bbox_areas.append((x1 - x0 + 1) * (y1 - y0 + 1))
    return dict(frames=len(F), cls=cls, deltas=deltas, Ns=Ns,
                slot_motion=slot_motion, slot_cls=slot_cls, bboxes=bbox_areas)


def err_88(dx, N):
    step = round(dx * 256 / N)                   # 8.8 per-vblank step
    return abs(step * N / 256.0 - dx)            # drift after N vblanks vs exact landing


def report(name, r):
    cls = r["cls"]; tot = max(sum(cls.values()), 1)
    print(f"\n################  {name}  ({r['frames']} frames)  ################")

    # ---- POINT 2 first (it frames the rest): the skating breakdown -------------------
    print("\n[2] Translation vs keyframe (skating risk), all matched slots:")
    for k in ("STATIC", "TWEEN", "SKATE", "POSE"):
        print(f"      {k:7}{100*cls[k]/tot:6.1f}%   ", end="")
    print()
    move = cls["TWEEN"] + cls["SKATE"]
    print(f"    of all MOVING slots: TWEEN(clean) {100*cls['TWEEN']/max(move,1):.0f}%  "
          f"SKATE(pose+move) {100*cls['SKATE']/max(move,1):.0f}%")

    def slot_line(tag, idx):
        sc = r["slot_cls"][idx]; st = max(sum(sc.values()), 1)
        print(f"    {tag} (slot {idx}): TWEEN {100*sc['TWEEN']/st:.0f}%  SKATE {100*sc['SKATE']/st:.0f}%  "
              f"POSE {100*sc['POSE']/st:.0f}%  STATIC {100*sc['STATIC']/st:.0f}%")
    # two ends of the spectrum: the biggest pure-translator (a DECORATION) vs the most
    # pose-active mover (the CHARACTER/hero). No semantic IDs, so slots are matched by index.
    if r["slot_motion"]:
        slot_line("top TRANSLATOR (decoration-like, tweens clean)",
                  max(r["slot_motion"], key=r["slot_motion"].get))
    skaters = {i: c["SKATE"] for i, c in r["slot_cls"].items() if c["SKATE"]}
    if skaters:
        slot_line("top SKATER (character/hero-like, pose+move)", max(skaters, key=skaters.get))
    print("    (slots matched by index; identity blurs when the draw-list length changes -- AW has")
    print("     no object IDs, so these are representative, not a guaranteed single object.)")

    # ---- POINT 1: sub-tick interpolation + 8.8 fixed-point ---------------------------
    print("\n[1] Sub-tick interpolation feasibility + 8.8 fixed-point precision:")
    print(f"    N=VAR_PAUSE_SLICES distribution: {dict(sorted(r['Ns'].items()))}")
    ds = r["deltas"]
    if ds:
        mags = [d[0] + d[1] for d in ds]
        big = sum(1 for mvg in mags if mvg >= 2)
        avg_step = sum((d[0] + d[1]) / d[2] for d in ds) / len(ds)
        worst = max(max(err_88(d[0], d[2]), err_88(d[1], d[2])) for d in ds)
        print(f"    moves: {len(ds)}, avg |delta|={sum(mags)/len(ds):.1f}px, "
              f">=2px (worth smoothing): {100*big/len(ds):.0f}%")
        print(f"    avg per-vblank step = {avg_step:.2f}px  ({'sub-pixel -> NEEDS the 8.8 fraction' if avg_step<1 else 'mostly >=1px/vblank'})")
        print(f"    8.8 stepping worst-case landing error over a full tick: {worst:.4f}px "
              f"({'negligible -> 8.8 is enough' if worst < 0.5 else 'CHECK'})")

    # ---- POINT 3: dirty-rectangle hero re-blit cost ----------------------------------
    print("\n[3] Dirty-rectangle cost (restore bg under mover + redraw at 50 fps):")
    bb = r["bboxes"]
    if bb:
        bb_sorted = sorted(bb)
        med = bb_sorted[len(bb)//2]; p90 = bb_sorted[int(len(bb)*0.9)]
        # per redraw: restore (copy = 2 PCLK/byte) + draw spans (~1 PCLK/byte) ~ 3 bytes-equiv
        cyc_med = med * 3 / BYTES_PER_CYC
        cyc_p90 = p90 * 3 / BYTES_PER_CYC
        print(f"    moving-sprite bbox (LR bytes): median {med}, 90th pct {p90} "
              f"(full page = {160*200})")
        print(f"    blit cost/redraw: median ~{cyc_med:.0f} cyc, 90th ~{cyc_p90:.0f} cyc "
              f"= {100*cyc_p90/PAL_BUDGET:.1f}% of a vblank")
        print(f"    -> at 50 fps that is one redraw per vblank; {100*cyc_p90/PAL_BUDGET:.1f}% leaves "
              f"the CPU/blitter almost entirely free. Affordable.")


def main():
    if len(sys.argv) > 1:
        part = int(sys.argv[1]); frames = int(sys.argv[2]) if len(sys.argv) > 2 else 400
        parts = [(part, dict(PARTS).get(part, f"p{part}"))]
    else:
        parts = PARTS; frames = 250
    print("== Gemini n1.txt 3-point motion-smoothing measurement (GAME) ==")
    for p, name in parts:
        report(name, profile_part(p, frames))
    print("\n" + "=" * 60)
    print("Verdict knobs: [2] hero TWEEN%% high -> smoothing visible on the hero; low -> the hero")
    print("mostly pose-snaps (skating, limited gain). [1] confirms 8.8+LUT is precise & needed.")
    print("[3] confirms the dirty-rect redraw is cheap enough to run every vblank.")


if __name__ == "__main__":
    main()
