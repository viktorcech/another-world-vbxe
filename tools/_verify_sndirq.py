#!/usr/bin/env python3
"""_verify_sndirq -- bit-identity of the game's AND the intro's sound player (snd_play + the Timer-1 IRQ,
POKEY and COVOX modes), old build vs new build, on the cycle-exact 6502.

The OLD build is the reference build in out/bench/ref (tools/_ref_build.py). Each case starts a
sample with snd_play and then delivers IRQs one after another, exactly as the CPU enters
an IRQ (PC + P pushed, rti back), until the sample has ended and a few stray IRQs
followed. Everything the player does to the outside world is recorded in order -- every
write to I/O ($D000-$D7FF: AUDC4, AUDF1, the covox ports, MEMAC-B, IRQEN, STIMER) and to
POKMSK -- and must be identical; the cycles per IRQ are summed. The internal state
encoding is free to change (it is not compared).

    python tools/_verify_sndirq.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _cpu6502 as C                                              # noqa: E402

SENT = 0xFFF0
IRQST = 0xD20E

# (name, window start, length, bank-list index, covox)
CASES = [
    ('short, one page', 0x4510, 40, 0),
    ('page cross', 0x45F0, 300, 1),
    ('16 KB window cross', 0x7F80, 400, 2),
    ('odd length', 0x4401, 77, 3),
    ('long', 0x4000, 1500, 4),
]


class Run:
    def __init__(self, xex, lst):
        self.img = bytearray(65536)
        C.load_xex(self.img, open(xex, 'rb').read())
        self.L = C.labels(lst)

    def go(self, win, length, blidx, covox):
        mem = bytearray(self.img)
        cpu = C.CPU(mem)
        L = self.L
        log = []
        cyc_irq = [0]

        def wr(a, v):
            if 0xD000 <= a < 0xD800 or a == 0x10:
                log.append((a, v))
            return None

        def rd(a):
            if a == IRQST:
                return 0x00                      # Timer 1 pending (bit 0 = 0)
            return None
        cpu.wr_hook = wr
        cpu.rd_hook = rd
        for a in range(0x4000, 0x8000):          # the sample bytes (RAM stands in for VRAM)
            mem[a] = (a * 37 + 11) & 0xFF
        mem[0x10] = 0xC0                          # POKMSK: some serial bits, Timer 1 off
        mem[L['memb_cur']] = 0x95
        x = 0
        mem[L['snd_dir_winlo'] + x] = win & 0xFF
        mem[L['snd_dir_winhi'] + x] = win >> 8
        mem[L['snd_dir_lenlo'] + x] = length & 0xFF
        mem[L['snd_dir_lenhi'] + x] = length >> 8
        mem[L['snd_dir_blidx'] + x] = blidx

        def call(entry):
            cpu.sp = 0xFD
            r = SENT - 1
            cpu.push(r >> 8)
            cpu.push(r & 0xFF)
            cpu.pc = entry
            n = 0
            while cpu.pc != SENT:
                cpu.step()
                n += 1
                if n > 200000:
                    raise RuntimeError('runaway call')
        if covox:
            call(L['snd_go_covox'])
            entry = L['cv_irq']
        else:
            entry = L['snd_irq']
        log.append(('play',))
        cpu.x = x
        # snd_play expects X = the directory index
        cpu.sp = 0xFD
        r = SENT - 1
        cpu.push(r >> 8)
        cpu.push(r & 0xFF)
        cpu.pc = L['snd_play']
        cpu.x = x
        while cpu.pc != SENT:
            cpu.step()
        for k in range(2 * length + 6):
            log.append(('irq', k))
            cpu.sp = 0xFD
            cpu.push(SENT >> 8)
            cpu.push(SENT & 0xFF)
            cpu.push(cpu.flags())
            cpu.pc = entry
            c0 = cpu.cyc
            n = 0
            while cpu.pc != SENT:
                cpu.step()
                n += 1
                if n > 1000:
                    raise RuntimeError('runaway irq')
            cyc_irq[0] += cpu.cyc - c0
        return log, cyc_irq[0]


class IntroRun(Run):
    """the intro's two-voice player: music (half rate, AUDC2) + SFX (AUDC4)."""

    def go_intro(self, covox, sfx_at, mus_len, ticks):
        mem = bytearray(self.img)
        cpu = C.CPU(mem)
        L = self.L
        log = []
        cyc = [0]

        def wr(a, v):
            if 0xD000 <= a < 0xD800 or a == 0x10:
                log.append((a, v))

        def rd(a):
            return 0x00 if a == IRQST else None
        cpu.wr_hook = wr
        cpu.rd_hook = rd
        for a in range(0x4000, 0x8000):
            mem[a] = (a * 29 + 5) & 0xFF
        mem[0x10] = 0xC0
        mem[L['memb_cur']] = 0x95

        def call(entry, a=0, x=0):
            cpu.sp = 0xFD
            r = SENT - 1
            cpu.push(r >> 8)
            cpu.push(r & 0xFF)
            cpu.pc = entry
            cpu.a = a
            cpu.x = x
            n = 0
            while cpu.pc != SENT:
                cpu.step()
                n += 1
                if n > 200000:
                    raise RuntimeError('runaway call')
        if covox:
            call(L['snd_go_covox'])
            entry = L['cv_irq']
        else:
            entry = L['snd_irq']
        log.append(('mus_play',))
        call(L['mus_play'])
        neg = (0x1000000 - mus_len) & 0xFFFFFF            # a short music stream
        mem[L['mus_rem']] = neg & 0xFF
        mem[L['mus_rem'] + 1] = (neg >> 8) & 0xFF
        mem[L['mus_rem'] + 2] = neg >> 16
        for k in range(ticks):
            if k == sfx_at:
                log.append(('snd_play',))
                call(L['snd_play'], a=40, x=0)
            log.append(('irq', k))
            cpu.sp = 0xFD
            cpu.push(SENT >> 8)
            cpu.push(SENT & 0xFF)
            cpu.push(cpu.flags())
            cpu.pc = entry
            c0 = cpu.cyc
            n = 0
            while cpu.pc != SENT:
                cpu.step()
                n += 1
                if n > 1000:
                    raise RuntimeError('runaway irq')
            cyc[0] += cpu.cyc - c0
        return log, cyc[0]


INTRO_CASES = [
    # (name, SFX start tick, music bytes, ticks)
    ('music, SFX mid-way', 300, 700, 4000),
    ('SFX while the music ends', 100, 120, 3000),
    ('SFX after the music', 900, 150, 4000),
]


def main():
    ref = os.path.join(os.path.dirname(HERE), 'out', 'bench', 'ref')
    proj = os.path.dirname(HERE)
    old = Run(os.path.join(ref, 'awgame.xex'), os.path.join(ref, 'awgame.lst'))
    new = Run(os.path.join(proj, 'awgame.xex'), os.path.join(proj, 'out', 'awgame.lst'))
    ok = True
    for covox in (False, True):
        for name, win, length, blidx in CASES:
            lo, co = old.go(win, length, blidx, covox)
            ln, cn = new.go(win, length, blidx, covox)
            same = lo == ln
            ok &= same
            print('  %-6s %-22s %5d IRQs  cycles old=%7d new=%7d (%+.1f%%)  %s' % (
                'COVOX' if covox else 'POKEY', name, 2 * length + 6, co, cn,
                100.0 * (cn - co) / co, 'identical' if same else '*** WRITES DIFFER ***'))
            if not same:
                for i, (a, b) in enumerate(zip(lo, ln)):
                    if a != b:
                        print('     first difference at event %d: %r vs %r' % (i, a, b))
                        break
    iold = IntroRun(os.path.join(ref, 'awintro.xex'), os.path.join(ref, 'awintro.lst'))
    inew = IntroRun(os.path.join(proj, 'awintro.xex'), os.path.join(proj, 'out', 'awintro.lst'))
    for covox in (False, True):
        for name, at, mlen, ticks in INTRO_CASES:
            lo, co = iold.go_intro(covox, at, mlen, ticks)
            ln, cn = inew.go_intro(covox, at, mlen, ticks)
            same = lo == ln
            ok &= same
            print('  INTRO %-6s %-24s %5d IRQs  cycles old=%7d new=%7d (%+.1f%%)  %s' % (
                'COVOX' if covox else 'POKEY', name, ticks, co, cn, 100.0 * (cn - co) / co,
                'identical' if same else '*** WRITES DIFFER ***'))
            if not same:
                for i, (a, b) in enumerate(zip(lo, ln)):
                    if a != b:
                        print('     first difference at event %d: %r vs %r' % (i, a, b))
                        break
    print('RESULT:', 'OK - bit-identical' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
