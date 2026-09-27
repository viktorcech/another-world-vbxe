#!/usr/bin/env python3
"""_prof_diff -- per-proc cycles/frame, gate baseline vs the last gate run (all parts).

    python tools/_prof_diff.py [part|all] [top]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _lst6502 as LS                                             # noqa: E402

OUT = os.path.join(PROJ, 'out', 'bench')
PARTS = [16001, 16002, 16003, 16004, 16005, 16006, 16007]


def per_proc(files, lst):
    ins = sorted((i for i in (LS.read('game') if lst == 'game' else LS.read('game', lst))
                  if not i.data and i.mn), key=lambda i: i.addr)
    own, cur = {}, '?'
    for i in ins:
        cur = next((n for n in i.labels if not n.startswith('?')), cur)
        own[i.addr] = cur
    agg = {}
    for f in files:
        for a, c in json.load(open(f)).items():
            p = own.get(int(a), '?')
            agg[p] = agg.get(p, 0) + c
    return agg


def main():
    sel = sys.argv[1] if len(sys.argv) > 1 else 'all'
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    parts = PARTS if sel == 'all' else [int(sel)]
    # proc names come from the CURRENT listing for both (addresses of the baseline may
    # have moved: the baseline is attributed by its own saved listing when present)
    now = per_proc([os.path.join(OUT, 'prof_%d.json' % p) for p in parts], 'game')
    bl = os.path.join(OUT, 'base_awgame.lst')
    base = per_proc([os.path.join(OUT, 'prof_%d_base.json' % p) for p in parts],
                    bl if os.path.exists(bl) else 'game')
    keys = sorted(set(now) | set(base), key=lambda k: -abs(now.get(k, 0) - base.get(k, 0)))
    for k in keys[:top]:
        print('  %-24s %9.0f -> %9.0f  %+8.0f' % (k, base.get(k, 0), now.get(k, 0), now.get(k, 0) - base.get(k, 0)))


if __name__ == '__main__':
    main()
