#!/usr/bin/env python3
"""_smc_imm -- 6502-idioms "keep a variable as the operand of lda # / cmp # (2 cycles)":
every plain variable of the game and the intro, priced from the frame profiles.

A variable V qualifies when it is only ever touched by plain (non-indexed, non-pointer)
loads/ALU reads and plain stores (sta/stx/sty); no read-modify-write, no indexed or
indirect access that could reach it, not a zero-page pointer byte, not I/O / VRAM.
Converting it: every read site becomes `op #imm` and every write stores into all the
read sites' operand bytes instead of into V.

    saved/frame = sum(reads x (3 or 4 - 2)) - sum(writes x (4 x n_reads - old store cost))

Only candidates with a positive saving are listed, best first.

    python tools/_smc_imm.py [game|intro|both]
"""
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _lst6502 as LS                                             # noqa: E402

READ = {'lda', 'ldx', 'ldy', 'cmp', 'cpx', 'cpy', 'adc', 'sbc', 'and', 'ora', 'eor'}
STORE = {'sta', 'stx', 'sty'}
RMW = {'inc', 'dec', 'asl', 'lsr', 'rol', 'ror'}
PROF = {'game': 'prof_all_cnt.json', 'intro': 'iprof_new_cnt.json'}


def analyse(build):
    cnt = json.load(open(os.path.join(PROJ, 'out', 'bench', PROF[build])))
    ins = [i for i in LS.read(build) if i.mn and not i.data]
    code = set()
    for i in ins:
        code.update(range(i.addr, i.addr + i.size))
    reads, writes, bad = defaultdict(list), defaultdict(list), set()
    for i in ins:
        a = i.arg
        if a is None or i.mode in ('imm', 'rel', 'acc', 'imp'):
            continue
        if i.mode in ('zp', 'abs'):
            if i.mn in READ:
                reads[a].append(i)
            elif i.mn in STORE:
                writes[a].append(i)
            elif i.mn in RMW or i.mn == 'bit' or i.mn in ('jmp', 'jsr'):
                bad.add(a)
        elif i.mode in ('izy', 'izx', 'ind'):
            bad.update((a, a + 1))                         # pointer bytes
        elif i.mode in ('abx', 'aby', 'zpx', 'zpy'):
            bad.update(range(a, a + 256))                  # anything a table index reaches
    out = []
    for v in set(reads) | set(writes):
        if v in bad or v in code or 0xD000 <= v <= 0xD7FF or 0x4000 <= v <= 0x8FFF or v < 0x80:
            continue
        rs, ws = reads.get(v, []), writes.get(v, [])
        if not rs or not ws:
            continue
        rc = [cnt.get(str(i.addr), 0) for i in rs]
        wc = [cnt.get(str(i.addr), 0) for i in ws]
        if STATIC and not any(rc) and not any(wc):
            rc, wc = [1] * len(rs), [1] * len(ws)  # never profiled: each site once
        gain = sum(c * ((3 if i.mode == 'zp' else 4) - 2) for c, i in zip(rc, rs))
        cost = sum(c * (4 * len(rs) - (3 if i.mode == 'zp' else 4)) for c, i in zip(wc, ws))
        if gain - cost > 0.5:
            out.append((gain - cost, v, rs, ws, rc, wc))
    out.sort(key=lambda r: -r[0])
    lab = {}
    for i in LS.read(build):
        for n in i.labels:
            lab.setdefault(i.addr, n)
    L = LS.labels(build)
    names = defaultdict(list)
    for n, a in L.items():
        names[a].append(n)
    print('== %s: %d candidates' % (build, len(out)))
    for net, v, rs, ws, rc, wc in out:
        nm = ','.join(sorted(names.get(v, ['?']))[:2])
        print('  %8.1f cyc/frame  $%04X %-18s reads %d (%s)  writes %d (%s)' % (
            net, v, nm, len(rs), ' '.join('%s:%d=%.0f' % (os.path.basename(i.file), i.line, c)
                                          for i, c in zip(rs, rc)),
            len(ws), ' '.join('%s:%d=%.0f' % (os.path.basename(i.file), i.line, c) for i, c in zip(ws, wc))))


STATIC = '--static' in sys.argv


def main():
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    sel = a[0] if a else 'both'
    for b in (('game', 'intro') if sel == 'both' else (sel,)):
        analyse(b)


if __name__ == '__main__':
    main()
