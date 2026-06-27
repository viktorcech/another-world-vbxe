#!/usr/bin/env python3
"""
prof_motion.py - GAME: is a scene's motion TWEENABLE (smoothable) or keyframed POSE animation?

The game is vblank-paced: each displayed frame is held var[0xFF] (4-5) vblanks, so slow scenes
look choppy (~10 fps). A "motion smoothing" hack could redraw moving objects at in-between
positions over a held static background -- BUT only TRANSLATION can be tweened. If an object's
SHAPE id (off) changes every frame (keyframed pose animation), there is no in-between shape to
draw, so tweening cannot smooth it.

This measures which it is, WITHOUT touching the engine. The draw list is stable in order/count
within a scene (the bytecode emits the same objects in the same slots each tick), so we match
draws by slot index across consecutive frames and classify each:

  STATIC : off + x,y,zoom all unchanged          -> belongs in the cached background
  MOVE   : same off, position/zoom changed        -> TWEENABLE translation (record dx,dy,dz)
  POSE   : off changed                            -> keyframed shape anim, NOT tweenable

Then: how much motion is a uniform camera-pan (one shared delta -> trivial, high-value tween)
vs per-object (varied deltas -> fiddly), and how much is un-tweenable pose anim (the residual
choppiness no trick removes).

  python tools/prof_motion.py            # all parts, 250 frames
  python tools/prof_motion.py 16002 400  # water
"""
import os, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_atari

PARTS = [(16002, "water"), (16003, "jail"), (16004, "cite"),
         (16005, "arene"), (16006, "luxe"), (16007, "final")]


def key(e):
    # e = ('spr', off, x, y, zoom, bank) or ('bg', off, x, y, zoom)
    kind = e[0]; off = e[1]; x = e[2]; y = e[3]; zoom = e[4]
    return kind, off, x, y, zoom


def profile_part(part, frames):
    vm = game_atari.GameAtari(part)
    vm.run(frames)
    F = vm.frames
    n_static = n_move = n_pose = 0
    structural = 0                         # frame pairs where the slot count changed
    pan_share_sum = 0.0; pan_pairs = 0
    movers_per_frame = []; pose_per_frame = []
    for a, b in zip(F, F[1:]):
        da, db = a[4], b[4]
        if len(da) != len(db):
            structural += 1
        m = min(len(da), len(db))
        deltas = []
        mv = ps = 0
        for i in range(m):
            ea = da[i]; eb_ = db[i]
            # same slot kind+roughly same object identity?  off is the shape id.
            if ea[0] != eb_[0]:
                n_pose += 1; ps += 1; continue
            if ea[1] != eb_[1]:                       # off changed -> pose anim
                n_pose += 1; ps += 1
                # it may ALSO have translated, but the shape jump dominates -> not tweenable
                continue
            dx = eb_[2] - ea[2]; dy = eb_[3] - ea[3]; dz = eb_[4] - ea[4]
            if dx == 0 and dy == 0 and dz == 0:
                n_static += 1
            else:
                n_move += 1; mv += 1
                deltas.append((dx, dy, dz))
        movers_per_frame.append(mv); pose_per_frame.append(ps)
        if deltas:                              # camera-pan = one delta dominates the movers
            top = collections.Counter(deltas).most_common(1)[0][1]
            pan_share_sum += top / len(deltas); pan_pairs += 1
    tot = max(n_static + n_move + n_pose, 1)
    nf = max(len(movers_per_frame), 1)
    return dict(frames=len(F),
                static=100 * n_static / tot, move=100 * n_move / tot, pose=100 * n_pose / tot,
                movers=sum(movers_per_frame) / nf, poses=sum(pose_per_frame) / nf,
                pan=100 * pan_share_sum / max(pan_pairs, 1), struct=structural)


def main():
    if len(sys.argv) > 1:
        part = int(sys.argv[1]); frames = int(sys.argv[2]) if len(sys.argv) > 2 else 400
        parts = [(part, dict(PARTS).get(part, f"p{part}"))]
    else:
        parts = PARTS; frames = 250

    print("== motion classification: tweenable translation vs keyframed pose anim (GAME) ==\n")
    print(f"{frames} no-input frames/part. Draws matched by slot across consecutive frames.\n")
    print(f"  {'part':7}{'frames':>7}{'static%':>9}{'MOVE%':>8}{'POSE%':>8}"
          f"{'movers/fr':>11}{'pose/fr':>9}{'pan-share':>11}")
    for p, name in parts:
        r = profile_part(p, frames)
        print(f"  {name:7}{r['frames']:7}{r['static']:8.1f}%{r['move']:7.1f}%{r['pose']:7.1f}%"
              f"{r['movers']:11.1f}{r['poses']:9.1f}{r['pan']:10.0f}%")

    print()
    print("Reading it:")
    print("  STATIC% -> draw it once, cache as background (free, no tween needed).")
    print("  MOVE%   -> TWEENABLE: redraw at in-between positions for smooth motion.")
    print("  POSE%   -> keyframed shape animation; NO trick smooths it (the residual choppiness).")
    print("  pan-share: % of movers in a frame sharing ONE delta. High -> camera pan (cheap, big")
    print("            win: tween a single offset). Low -> per-object motion (fiddly per-sprite).")
    print()
    print("Verdict rule: smoothing pays off only if MOVE% is high AND POSE% is low. If POSE")
    print("dominates, the scene is choppy BY ART (keyframed) and no engine trick fixes it -- the")
    print("faithful AW look. High pan-share means do the cheap camera-only version, not per-sprite.")


if __name__ == "__main__":
    main()
