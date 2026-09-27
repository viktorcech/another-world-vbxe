#!/usr/bin/env python3
"""skill_prof -- where the cycles of a skill_measure poly case go (seconds).

Runs one of skill_measure's poly cases on the cycle-exact 6502 and attributes every
instruction's cycles to its routine (the nearest non-`?` label at or below it) and to
the instruction itself; the blitter's BUSY waits show up as the wait loop's lines.

    python tools/skill_prof.py [case-substring] [N hot lines]
"""
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import skill_measure as M                                         # noqa: E402
import _lst6502 as LS                                             # noqa: E402


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else 'trapezoid, half'
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    name, st_, dx, dy, zoom, half = next(p for p in M.POLY if pat in p[0])
    b = M.B()
    L = b.L
    ins = LS.read('game')
    at = {i.addr: i for i in ins if not i.data and i.mn}
    rout, cur = {}, '?'
    for i in sorted(at.values(), key=lambda x: x.addr):
        cur = next((n for n in i.labels if not n.startswith('?')), cur)
        rout[i.addr] = cur

    per_ins, per_rout, count = Counter(), Counter(), Counter()
    cpu, mem, st = b.fresh()
    mem[0x4000:0x4000 + len(st_)] = st_
    for k in ('hires', 'poly_base_adj', 'cc_baking', 'cc_flag'):
        if k in L:
            mem[L[k]] = 0
    mem[L['poly_bcb_h']] = half
    M.w16(mem, L['dr_zoom'], zoom)
    M.w16(mem, L['dr_x'], dx)
    M.w16(mem, L['dr_y'], dy)
    mem[L['dr_col']] = 0xFF
    M.w16(mem, L['dr_off'], 0)
    mem[L['psp']] = 0
    mem[L['last_scol']] = 0xFF
    mem[L['memb_cur']] = 0
    if zoom == 64:
        M.w16(mem, L['rs_smc'] + 1, L['rs_fast'])
    else:
        M.w16(mem, L['rs_smc'] + 1, L['rs_z4'])
        b.call(cpu, L['rs_z4_set'])
    b.call(cpu, L['set_poly_ptr'])
    cpu.cyc = 0
    cpu.sp = 0xFD
    r = (M.SENT - 1) & 0xFFFF
    cpu.push(r >> 8)
    cpu.push(r & 0xFF)
    cpu.pc = L['poly_draw']
    while cpu.pc != M.SENT:
        pc, c0 = cpu.pc, cpu.cyc
        cpu.step()
        d = cpu.cyc - c0
        per_ins[pc] += d
        count[pc] += 1
        per_rout[rout.get(pc, '$%04X' % pc)] += d
    tot = cpu.cyc
    print('%s: %d cycles, %d spans (%.1f / span)' % (name, tot, st['starts'], tot / max(1, st['starts'])))
    print('\nper routine:')
    for k, v in per_rout.most_common():
        print('  %-22s %7d  %5.1f%%' % (k, v, 100.0 * v / tot))
    print('\nhot instructions (cycles, executions):')
    for pc, v in per_ins.most_common(top):
        i = at.get(pc)
        src = i.src.strip()[:58] if i else ''
        where = '%s:%d' % (os.path.basename(i.file), i.line) if i else ''
        print('  $%04X %6d %5dx  %-20s %s' % (pc, v, count[pc], where, src))


if __name__ == '__main__':
    main()
