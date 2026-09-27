#!/usr/bin/env python3
"""skill_prof_intro -- where the INTRO's polygon cycles go (seconds).

Runs N random shape trees (the same generator skill_verify_intro uses) on the
cycle-exact 6502 and attributes every instruction's cycles to its routine.

    python tools/skill_prof_intro.py [N] [top]
"""
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _verify_skillpass as V                                     # noqa: E402
import _lst6502 as LS                                             # noqa: E402
import skill_verify_intro as SI                                   # noqa: E402


def main():
    import random
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 25
    b = V.Build(os.path.join(PROJ, 'awintro.xex'), os.path.join(PROJ, 'out', 'awintro.lst'))
    ins = LS.read('intro')
    at = {i.addr: i for i in ins if not i.data and i.mn}
    rout, cur = {}, '?'
    for i in sorted(at.values(), key=lambda x: x.addr):
        cur = next((nm for nm in i.labels if not nm.startswith('?')), cur)
        rout[i.addr] = cur

    per_rout, per_ins, count = Counter(), Counter(), Counter()
    tot = 0
    for seed in range(n):
        rng = random.Random(1000 + seed)
        stream = V.gen_tree(rng, rng.random() < 0.3)
        cpu, mem, log = b.fresh()
        SI.run_shape.__wrapped__ if False else None
        # set the same state skill_verify_intro uses, then step with attribution
        L = b.L
        b.call(cpu, L['upload_bcb'])
        log.clear()
        rng2 = random.Random(seed)
        mem[0x4000:0x4000 + len(stream)] = stream
        zoom = rng2.choice([64, 64, 64, 32, 48, 80, 128, 200, 300, 1000])
        for k, v in (('poly_base_adj', 0), ('hires', 0)):
            if k in L:
                mem[L[k]] = v
        if 'poly_bcb_h' in L:
            mem[L['poly_bcb_h']] = rng2.randrange(2)
        V.w16(mem, L['dr_zoom'], zoom)
        V.w16(mem, L['dr_x'], rng2.randrange(-40, 360) & 0xFFFF)
        V.w16(mem, L['dr_y'], rng2.randrange(-40, 240) & 0xFFFF)
        mem[L['dr_col']] = rng2.randrange(256)
        V.w16(mem, L['dr_off'], 0)
        mem[L['psp']] = 0
        mem[L['last_scol']] = 0xFF
        mem[L['memb_cur']] = 0
        if zoom == 64:
            V.w16(mem, L['rs_smc'] + 1, L['rs_fast'])
        else:
            V.w16(mem, L['rs_smc'] + 1, L['rs_slow'])
        b.call(cpu, L['set_poly_ptr'])
        cpu.sp = 0xFD
        r = (V.SENT - 1) & 0xFFFF if hasattr(V, 'SENT') else 0xFFEF
        cpu.push(r >> 8)
        cpu.push(r & 0xFF)
        cpu.pc = L['poly_draw']
        cpu.cyc = 0
        stop = (r + 1) & 0xFFFF
        while cpu.pc != stop:
            pc, c0 = cpu.pc, cpu.cyc
            cpu.step()
            d = cpu.cyc - c0
            per_ins[pc] += d
            count[pc] += 1
            per_rout[rout.get(pc, '$%04X' % pc)] += d
        tot += cpu.cyc
    print('INTRO poly_draw: %d trees, %d cycles' % (n, tot))
    print('\nper routine:')
    for k, v in per_rout.most_common(top):
        print('  %-22s %8d  %5.1f%%' % (k, v, 100.0 * v / tot))
    print('\nhot instructions:')
    for pc, v in per_ins.most_common(top):
        i = at.get(pc)
        where = '%s:%d' % (os.path.basename(i.file), i.line) if i else ''
        print('  $%04X %7d %6dx  %-22s %s' % (pc, v, count[pc], where,
                                              (i.src.strip()[:52] if i else '')))


if __name__ == '__main__':
    main()
