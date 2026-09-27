#!/usr/bin/env python3
"""_skill_hot -- the 6502-idioms / rapidus / vbxe skill rules that need the PROFILE, over
the whole game, weighted by real executions (out/bench/prof_all*.json from
tools/_gate_parts.py: cycles and executions per frame, summed over the parts):

  TAKEN   a branch taken on most passes: the common path should fall through
          ("rare case out of line, not-taken branch": 2 vs 3 cycles)
  XPAGE   a branch or indexed read paying the page-cross +1 (taken branch across a
          page, abs,x / abs,y / (zp),y read crossing): align the table / loop
  JMP     a hot `jmp abs` (layout so the code falls through instead)
  CALL    a hot jsr whose callee is short (inline it: jsr+rts = 12)

    python tools/_skill_hot.py [min cycles/frame] [top]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _lst6502 as LS                                             # noqa: E402

OUT = os.path.join(PROJ, 'out', 'bench')


def main():
    lim = float(sys.argv[1]) if len(sys.argv) > 1 else 50
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    cyc = {int(a): v for a, v in json.load(open(os.path.join(OUT, 'prof_all.json'))).items()}
    cnt = {int(a): v for a, v in json.load(open(os.path.join(OUT, 'prof_all_cnt.json'))).items()}
    ins = [i for i in LS.read('game') if not i.data and i.mn]
    at = {i.addr: i for i in ins}
    rows = {'TAKEN': [], 'XPAGE': [], 'JMP': [], 'CALL': []}
    for a, n in cnt.items():
        i = at.get(a)
        if i is None or n <= 0:
            continue
        c = cyc.get(a, 0)
        base = LS.cycles(i.mn, i.mode)
        where = '%s:%d' % (os.path.basename(i.file), i.line)
        src = i.src.split(';')[0].strip()[:44]
        if i.mn in LS.BRANCH:
            extra = c - 2 * n                       # taken +1, page-crossing taken +2
            tgt = i.arg
            cross = tgt is not None and ((a + 2) ^ tgt) & 0xFF00
            taken = extra / (2 if cross else 1)
            if cross and extra >= lim / 4:
                rows['XPAGE'].append((extra / 2, where, src, 'taken %.0f/%.0f' % (taken, n)))
            if taken > n / 2 and taken - (n - taken) >= lim / 4:
                rows['TAKEN'].append((taken - (n - taken), where, src, 'taken %.0f/%.0f' % (taken, n)))
        elif i.mode in ('abx', 'aby', 'izy') and i.mn not in ('sta', 'stx', 'sty', 'asl', 'lsr', 'rol',
                                                             'ror', 'inc', 'dec'):
            extra = c - base * n
            if extra >= lim / 4:
                rows['XPAGE'].append((extra, where, src, '%.0f of %.0f reads cross' % (extra, n)))
        elif i.mn == 'jmp' and i.mode == 'abs' and 3 * n >= lim:
            rows['JMP'].append((3 * n, where, src, '%.0f x' % n))
        elif i.mn == 'jsr' and 12 * n >= lim:
            rows['CALL'].append((12 * n, where, src, '%.0f x' % n))
    for k, v in rows.items():
        print('-- %s' % k)
        for w, where, src, note in sorted(v, key=lambda r: -r[0])[:top]:
            print('  %8.0f  %-28s %-46s %s' % (w, where, src, note))


if __name__ == '__main__':
    main()
