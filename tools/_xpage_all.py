#!/usr/bin/env python3
"""_xpage_all -- every page-crossing penalty the frame profiles actually paid, game AND
intro: taken branches whose target lies in another page than the next instruction
(+1 per taken pass) and abs,x / abs,y / (zp),y reads that crossed (+1 per crossing
read). Cycles per frame, largest first -- what a layout guard (nocross) must fix.

    python tools/_xpage_all.py [min cycles/frame, default 0.5]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _lst6502 as LS                                             # noqa: E402

OUT = os.path.join(PROJ, 'out', 'bench')
PROF = {'game': ('prof_all.json', 'prof_all_cnt.json'),
        'intro': ('iprof_new.json', 'iprof_new_cnt.json')}


def run(build, mn):
    cyc = json.load(open(os.path.join(OUT, PROF[build][0])))
    cnt = json.load(open(os.path.join(OUT, PROF[build][1])))
    rows = []
    for i in LS.read(build):
        if not i.mn or i.data:
            continue
        n = cnt.get(str(i.addr), 0)
        if not n:
            continue
        c = cyc.get(str(i.addr), 0)
        if i.mn in LS.BRANCH and i.arg is not None:
            if ((i.addr + 2) ^ i.arg) & 0xFF00:
                pen = (c - 2 * n) / 2.0                  # taken passes (each 3+1 = 2 over 2)
                if pen >= mn:
                    rows.append((pen, i, 'branch'))
        elif i.mode in ('abx', 'aby', 'izy') and not i.mn.startswith('st'):
            base = LS.cycles(i.mn, i.mode)
            if base:
                pen = c - n * base
                if pen >= mn:
                    rows.append((pen, i, 'read'))
    rows.sort(key=lambda r: -r[0])
    print('== %s: %.0f cycles/frame on page crossings (%d sites)' % (build, sum(r[0] for r in rows), len(rows)))
    for pen, i, kind in rows:
        print('  %8.1f  %-6s %-28s %s' % (pen, kind, '%s:%d' % (os.path.basename(i.file), i.line),
                                         i.src.strip()[:50]))


def main():
    mn = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    run('game', mn)
    run('intro', mn)


if __name__ == '__main__':
    main()
