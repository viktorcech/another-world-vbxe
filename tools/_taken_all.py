#!/usr/bin/env python3
"""_taken_all -- 6502-idioms "rare case out of line, not-taken branch", over EVERY forward
conditional branch the profiles executed, game AND intro (not a top-N): each branch
taken on most of its executions is listed with its file:line, executions per frame and
the taken share. Loop back-edges and blitter/VBL wait spins are skipped (their taken
direction is the loop).

Profiles: out/bench/prof_all.json + prof_all_cnt.json (tools/_gate_parts.py) and
out/bench/iprof_new.json + iprof_new_cnt.json (tools/_bench_intro.py --dump ...).

    python tools/_taken_all.py [min executions per frame, default 0.5]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _lst6502 as LS                                             # noqa: E402

OUT = os.path.join(PROJ, 'out', 'bench')
WAIT = (0xD653, 0x14, 0xD40B, 0xD20F, 0xD01F)   # BLITTER BUSY, RTCLOK, VCOUNT, SKSTAT, CONSOL


def run(build, cyc_f, cnt_f, mn_exec):
    cyc = json.load(open(os.path.join(OUT, cyc_f)))
    cnt = json.load(open(os.path.join(OUT, cnt_f)))
    ins = [i for i in LS.read(build) if i.mn and not i.data]
    ins.sort(key=lambda i: i.addr)
    rows = []
    for k, i in enumerate(ins):
        if i.mn not in LS.BRANCH or i.arg is None or i.arg <= i.addr:
            continue
        n = cnt.get(str(i.addr), 0)
        if n < mn_exec:
            continue
        c = cyc.get(str(i.addr), 0)
        cross = ((i.addr + 2) ^ i.arg) & 0xFF00 != 0
        taken = (c - 2 * n) / (2.0 if cross else 1.0)
        if taken <= n / 2:
            continue
        prev = ins[max(0, k - 3):k]
        if any(p.arg in WAIT for p in prev if p.mn in ('lda', 'bit', 'ora', 'and')):
            continue
        rows.append((taken, n, i, cross))
    rows.sort(key=lambda r: -r[0])
    print('== %s: %d forward branches taken on most passes' % (build, len(rows)))
    for taken, n, i, cross in rows:
        print('  %8.1f/%8.1f  %-28s %s%s' % (taken, n, '%s:%d' % (os.path.basename(i.file), i.line),
                                             i.src.strip()[:44], '  [crosses]' if cross else ''))


def main():
    m = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    run('game', 'prof_all.json', 'prof_all_cnt.json', m)
    run('intro', 'iprof_new.json', 'iprof_new_cnt.json', m)


if __name__ == '__main__':
    main()
