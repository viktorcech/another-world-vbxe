#!/usr/bin/env python3
"""_wait_audit -- the vbxe-blitter skill's "measure every one": every BL_BUSY wait
loop in the GAME build, its spin cycles/frame per part (out/bench/prof_*.json from
tools/_gate_parts.py), and which START it guards (the next BL_START store, statically).

    python tools/_wait_audit.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _lst6502 as LS                                             # noqa: E402

OUT = os.path.join(PROJ, 'out', 'bench')
BL = 0xD653
PARTS = [16001, 16002, 16003, 16004, 16005, 16006, 16007]


def main():
    ins = [i for i in LS.read('game') if not i.data and i.mn]
    prof = {p: {int(a): v for a, v in
                json.load(open(os.path.join(OUT, 'prof_%d.json' % p))).items()} for p in PARTS}
    cnt = {p: {int(a): v for a, v in
               json.load(open(os.path.join(OUT, 'prof_%d_cnt.json' % p))).items()} for p in PARTS}
    sites = []
    k = 0
    while k < len(ins):
        i = ins[k]
        if i.mn == 'lda' and i.mode == 'abs' and i.arg == BL:
            body = [i.addr]
            j = k + 1
            while j < len(ins) and ins[j].mn in ('ora', 'lda', 'bne', 'beq') and j - k < 4:
                body.append(ins[j].addr)
                if ins[j].mn in ('bne', 'beq'):
                    break
                j += 1
            # the START this wait guards: the next BL_START store downstream
            start = None
            for m in range(j + 1, min(len(ins), j + 40)):
                if ins[m].mn == 'sta' and ins[m].mode == 'abs' and ins[m].arg == BL:
                    start = '%s:%d' % (os.path.basename(ins[m].file), ins[m].line)
                    break
                if ins[m].mn in ('rts', 'rti'):
                    start = 'none (caller: CPU touches what the blit wrote)'
                    break
            sites.append(('%s:%d' % (os.path.basename(i.file), i.line),
                          i.src.split(';')[0].strip()[:28], body, start))
            k = j + 1
            continue
        k += 1
    hdr = '%-26s %-28s' % ('wait (file:line)', 'head')
    print(hdr + ''.join('%9d' % (p % 100) for p in PARTS) + '     total  -> guards START at')
    tot = [0.0] * len(PARTS)
    for where, src, body, start in sites:
        row = [sum(prof[p].get(a, 0) for a in body) for p in PARTS]
        for x in range(len(PARTS)):
            tot[x] += row[x]
        passes = sum(cnt[p].get(body[0], 0) for p in PARTS)
        print('%-26s %-28s' % (where, src) + ''.join('%9.0f' % r for r in row)
              + '%10.0f' % sum(row) + '  -> %s  (%.0f polls/f)' % (start, passes))
    print('%-54s' % 'TOTAL' + ''.join('%9.0f' % t for t in tot) + '%10.0f' % sum(tot))


if __name__ == '__main__':
    main()
