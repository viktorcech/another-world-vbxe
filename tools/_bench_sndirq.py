"""_bench_sndirq.py - run the sound player's Timer-1 IRQ handler (snd_irq, and the covox
entry cv_irq) of the ORIGINAL and the CURRENT awgame.xex on the 6502 core, entered the
way the CPU enters an IRQ, for every state the handler has (phase 0, phase 1, phase 1
with a page cross, phase 1 with a 16 KB window cross, sample end, stray-after-silence),
and compare the WRITES (AUDC4 / MEMAC-B / IRQEN / POKMSK / covox ports / state cells)
plus the cycle count per IRQ.

    python tools/_bench_sndirq.py
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _cpu6502 as cpu6502
import _verify_skillpass as V

IRQEN = 0xD20E
SENT = 0xFFF0


def run_irq(b, entry, state):
    cpu, mem, log = b.fresh()
    L = b.L
    writes = []
    def wr(a, v):
        if a >= 0xD000 or a in (L['snd_active'], L['zsnd_cur'], L['zsnd_bank'], L['memb_cur'], 0x10) \
                or L['snd_rem'] <= a < L['snd_rem'] + 2 or L['snd_rd'] + 1 <= a <= L['snd_rd'] + 2 \
                or a == L['snd_blidx']:
            writes.append((a, v))
    def rd(a):
        if a == IRQEN:
            return 0x00              # IRQST: bit 0 = 0 -> Timer 1 pending (ours)
        return None
    cpu.wr_hook = wr; cpu.rd_hook = rd
    mem[0x10] = 0xC1                 # POKMSK: timer 1 + some serial bits
    mem[L['memb_cur']] = 0x95
    mem[L['zsnd_bank']] = 0x8E
    mem[L['snd_active']] = state['active']
    mem[L['zsnd_cur']] = state.get('cur', 0x5A)
    V.w16(mem, L['snd_rem'], state.get('rem', 0xFF00))
    mem[L['snd_rd'] + 1] = state.get('rd_lo', 0x10)
    mem[L['snd_rd'] + 2] = state.get('rd_hi', 0x45)
    mem[L['snd_blidx']] = 2
    mem[0x4510] = 0xA7               # the sample byte the phase-0 read fetches (RAM stands in for VRAM)
    mem[0x4500 + 0xFF] = 0x3C
    if 'cv_next' in L:
        mem[L['cv_next']] = 0x70
    # enter like an IRQ: push PC (sentinel) and P, then jump to the handler
    cpu.sp = 0xFD
    cpu.push(SENT >> 8); cpu.push(SENT & 0xFF); cpu.push(cpu.flags())
    cpu.pc = L[entry]; cpu.cyc = 0
    n = 0
    while cpu.pc != SENT:
        cpu.step(); n += 1
        if n > 10000:
            raise RuntimeError('runaway')
    return writes, cpu.cyc


STATES = [
    ('phase 0 (read byte, hi nibble)', dict(active=1)),
    ('phase 1 (lo nibble, count, advance)', dict(active=2)),
    ('phase 1, page cross', dict(active=2, rd_lo=0xFF)),
    ('phase 1, 16 KB window cross', dict(active=2, rd_lo=0xFF, rd_hi=0x7F)),
    ('phase 1, sample end (rem -> 0)', dict(active=2, rem=0xFFFF)),
    ('stray after silence (active = 0)', dict(active=0)),
]


def main():
    old = V.Build(*V.OLD); new = V.Build(*V.NEW)
    ok = True
    for entry in ('snd_irq', 'cv_irq'):
        print('== %s ==' % entry)
        print('   %-40s %8s %8s' % ('state', 'old cyc', 'new cyc'))
        for name, st in STATES:
            wo, co = run_irq(old, entry, st); wn, cn = run_irq(new, entry, st)
            same = wo == wn
            ok = ok and same
            print('   %-40s %8d %8d  %s' % (name, co, cn, 'writes identical' if same else 'WRITES DIFFER: %r vs %r' % (wo, wn)))
    print('RESULT:', 'OK - identical side effects' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
