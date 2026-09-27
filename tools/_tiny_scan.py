#!/usr/bin/env python3
"""_tiny_scan -- the two 6502-cycles-layout sweeps that need the whole call graph:

  INLINE  a jsr whose callee is a straight line of <= N instructions ending in rts
          (no branches, no other entry): inlining kills the 12-cycle jsr+rts.
  UNROLL  a backward dex/dey+bne loop whose body is 1-2 instructions: the 5-cycle
          loop edge dominates; unrolling pays if the count is fixed and small.

Both weighted by real executions (out/bench/prof_*_cnt.json from tools/_gate_parts.py).

    python tools/_tiny_scan.py [max callee instructions, default 6]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _lst6502 as LS                                             # noqa: E402

PARTS = [16001, 16002, 16003, 16004, 16005, 16006, 16007]


def main():
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    cnt = {}
    for p in PARTS:
        for a, v in json.load(open(os.path.join(PROJ, 'out', 'bench', 'prof_%d_cnt.json' % p))).items():
            cnt[int(a)] = cnt.get(int(a), 0) + v
    ins = [i for i in LS.read('game') if not i.data and i.mn]
    at = {i.addr: k for k, i in enumerate(ins)}

    static = '--static' in sys.argv        # no profile filter: EVERY file, cold code too
    print('-- INLINE (callee <= %d straight instructions ending rts)' % lim)
    rows = []
    for i in ins:
        if i.mn != 'jsr' or i.arg not in at:
            continue
        n = cnt.get(i.addr, 0)
        if n <= 0 and not static:
            continue
        n = max(n, 1)
        k = at[i.arg]
        body = []
        ok = False
        for j in range(k, k + lim + 1):
            m = ins[j]
            if m.mn == 'rts':
                ok = True
                break
            if m.mn in LS.BRANCH or m.mn in ('jmp', 'jsr', 'rti'):
                break
            body.append(m.mn)
        if ok and len(body) <= lim:
            rows.append((12 * n, '%s:%d' % (os.path.basename(i.file), i.line),
                         i.src.split(';')[0].strip()[:40], len(body), n))
    for sc, w, s, sz, n in sorted(rows, reverse=True)[:12]:
        print('  %8.0f cyc/f  %-26s %-40s callee=%d instr, %.0f calls/f' % (sc, w, s, sz, n))

    print('-- UNROLL (dex/dey/inx/iny + bne back, body <= 2 instructions)')
    rows = []
    for k, i in enumerate(ins):
        if i.mn != 'bne' or i.arg is None or i.arg >= i.addr:
            continue
        if at.get(i.arg) is None:
            continue
        body = ins[at[i.arg]:k]
        if not body or len(body) > 4:
            continue
        steps = [m.mn for m in body]
        if not any(s in ('dex', 'dey', 'inx', 'iny') for s in steps):
            continue
        n = cnt.get(i.addr, 0)
        if n <= 0 and not static:
            continue
        n = max(n, 1)
        rows.append((5 * n, '%s:%d' % (os.path.basename(i.file), i.line),
                     ' / '.join(steps)[:40], n))
    for sc, w, s, n in sorted(rows, reverse=True)[:12]:
        print('  %8.0f cyc/f  %-26s body: %-40s %.0f passes/f' % (sc, w, s, n))


if __name__ == '__main__':
    main()
