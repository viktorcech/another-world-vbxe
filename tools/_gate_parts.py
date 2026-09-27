#!/usr/bin/env python3
"""_gate_parts -- the whole-game gate: _bench_frame.py on every game part in parallel.

VIDEOSHA + VMSHA per part must stay identical to the saved baseline (bit-identity of
what the game shows and computes); CPU work / frame is the cost. Also writes the merged
per-address profile (out/bench/prof_all.json, cycles per frame summed over the parts).

    python tools/_gate_parts.py [save]      save = make the current build the baseline
"""
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
OUT = os.path.join(PROJ, 'out', 'bench')
PARTS = [16001, 16002, 16003, 16004, 16005, 16006, 16007]


def run(p):
    o = subprocess.run([sys.executable, os.path.join(HERE, '_bench_frame.py'), '--frames', '60',
                        '--warm', '20', '--part', str(p), '--top', '0',
                        '--dump', os.path.join(OUT, 'prof_%d.json' % p)],
                       capture_output=True, text=True, cwd=PROJ).stdout
    g = lambda rx: re.search(rx, o).group(1)                       # noqa: E731
    return p, dict(work=float(g(r'CPU work / frame\s+(\d+)')),
                   wait=float(g(r'blitter-wait cycles/frame\s*(\d+)')),
                   video=g(r'VIDEOSHA (\w+)'), vm=g(r'VMSHA\s+(\w+)'))


def main():
    os.makedirs(OUT, exist_ok=True)
    with ThreadPoolExecutor(len(PARTS)) as ex:
        res = dict(ex.map(run, PARTS))
    bf = os.path.join(OUT, 'gate_base.json')
    if sys.argv[1:] == ['save'] or not os.path.exists(bf):
        json.dump(res, open(bf, 'w'), indent=1)
        for p in PARTS:                               # the baseline's profiles, for diffs
            src = os.path.join(OUT, 'prof_%d.json' % p)
            open(os.path.join(OUT, 'prof_%d_base.json' % p), 'w').write(open(src).read())
        open(os.path.join(OUT, 'base_awgame.lst'), 'w', encoding='latin-1').write(
            open(os.path.join(PROJ, 'out', 'awgame.lst'), encoding='latin-1').read())
        print('baseline saved')
    base = {int(k): v for k, v in json.load(open(bf)).items()}
    lf = os.path.join(OUT, 'gate_last.json')           # the previous build: THIS step's saving
    last = {int(k): v for k, v in json.load(open(lf)).items()} if os.path.exists(lf) else None
    json.dump(res, open(lf, 'w'), indent=1)
    tw = tb = 0
    ok = True
    for p in PARTS:
        b, r = base[p], res[p]
        same = b['video'] == r['video'] and b['vm'] == r['vm']
        ok &= same
        tw += r['work']
        tb += b['work']
        print('%d  work %7.0f -> %7.0f (%+6.0f)  wait %5.0f -> %5.0f  %s'
              % (p, b['work'], r['work'], r['work'] - b['work'], b['wait'], r['wait'],
                 'OK' if same else '*** VIDEO/VM DIFF ***'))
    print('total work %.0f -> %.0f (%+.0f, %.2f%%)  %s'
          % (tb, tw, tw - tb, 100 * (tw - tb) / tb, 'ALL IDENTICAL' if ok else 'MISMATCH'))
    if last:
        tl = sum(last[p]['work'] for p in PARTS)
        print('THIS STEP (vs the previous build): %.0f -> %.0f  = %+.0f cycles / frame (sum of parts)'
              % (tl, tw, tw - tl))
    agg = {}
    for p in PARTS:
        for a, c in json.load(open(os.path.join(OUT, 'prof_%d.json' % p))).items():
            agg[a] = agg.get(a, 0) + c
    json.dump(agg, open(os.path.join(OUT, 'prof_all.json'), 'w'))
    agg = {}
    for p in PARTS:
        for a, c in json.load(open(os.path.join(OUT, 'prof_%d_cnt.json' % p))).items():
            agg[a] = agg.get(a, 0) + c
    json.dump(agg, open(os.path.join(OUT, 'prof_all_cnt.json'), 'w'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
