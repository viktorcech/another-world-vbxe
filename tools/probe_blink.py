#!/usr/bin/env python3
"""
probe_blink.py - inspect the intro around a frame where the user sees a
dropped frame / blink (default 911): per-frame stats from the float VM pass
(pal, hold, draws, pixel-diff vs previous frame) plus the raw event slice
between the surrounding BLITs, to localise what the VM emits there.

    python probe_blink.py [center] [radius]
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aw_sim


def main():
    center = int(sys.argv[1]) if len(sys.argv) > 1 else 911
    radius = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    vm = aw_sim.VM('float')
    vm.run(100000)
    frames = vm.frames
    print(f'total frames: {len(frames)}')
    lo = max(0, center - radius); hi = min(len(frames) - 1, center + radius)

    prev = None
    for i in range(lo, hi + 1):
        pg, pal, hold, draws, dl = frames[i]
        d = sum(1 for a, b in zip(pg, prev) if a != b) if prev is not None else 0
        nz = sum(1 for c in pg if c)
        print(f'f{i:4}  pal={pal:2} hold={hold:3} draws={draws:3} '
              f'diff-vs-prev={d:6}  non-bg={nz*100//aw_sim.SIZE:3}%')
        prev = pg

    # event slice: walk events, counting blits, print everything between
    # blit lo-1 and blit hi+1 (compact; polys as one summary line per frame)
    print('\n--- event stream around the window ---')
    nblit = 0
    pend = []
    for e in vm.events:
        if e[0] == 'blit':
            if lo - 1 <= nblit <= hi + 1:
                # summarize the polys, print other events verbatim
                polys = [x for x in pend if x[0] == 'poly']
                other = [x for x in pend if x[0] != 'poly']
                for o in other:
                    print(f'   {o}')
                if polys:
                    print(f'   ({len(polys)} poly draws)')
                print(f'BLIT #{nblit}: page={e[1]} hold={e[2]}')
            pend = []
            nblit += 1
            if nblit > hi + 1:
                break
        else:
            pend.append(e)


if __name__ == '__main__':
    main()
