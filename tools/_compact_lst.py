#!/usr/bin/env python3
"""_compact_lst -- the ASSEMBLED code only (no inactive .else, no comments), one line
per instruction, per source file, with cycles/frame from a profile json.

    python tools/_compact_lst.py game|intro [profile.json|-]  -> out/bench/compact_<which>/
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _lst6502 as LS                                             # noqa: E402


def main():
    which = sys.argv[1]
    prof = json.load(open(sys.argv[2])) if len(sys.argv) > 2 and sys.argv[2] != '-' else {}
    out = os.path.join(PROJ, 'out', 'bench', 'compact_' + which)
    os.makedirs(out, exist_ok=True)
    byf = collections.defaultdict(list)
    for i in LS.read(which):
        byf[i.file].append(i)
    for f, L in byf.items():
        L.sort(key=lambda i: (i.line, i.addr))
        rows = []
        for i in L:
            src = (i.src.split(';')[0] if i.src else '').strip()
            c = prof.get('%d' % i.addr, 0)
            if i.data:
                rows.append('%5d $%04X %8s  %s' % (i.line, i.addr, '', src[:70]))
            else:
                rows.append('%5d $%04X %8s  %-14s %s%s' % (i.line, i.addr, ('%.0f' % c) if c else '',
                            ','.join(i.labels)[:14], src[:80], ' [M]' if i.macro else ''))
        open(os.path.join(out, os.path.basename(f) + '.txt'), 'w').write('\n'.join(rows) + '\n')


if __name__ == '__main__':
    main()
