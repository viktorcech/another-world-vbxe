#!/usr/bin/env python3
"""_sim_intro6502.py - run the WHOLE assembled intro (awintro.xex) on the Python 6502
core with a minimal VBXE model (MEMAC-A/B windows into a 512 KB VRAM, the blitter
executing every BCB fire, RTCLOK3 ticking once per PAL frame). No sound, no IRQ,
no ANTIC. The pre-intro menu / sound player are stubbed to `rts`.

Purpose (2026-09-10): the intro fails ONLY under Rapidus. The only Rapidus-specific
switch in the code is detect_cpu -> poly_bcb_h (0 = full detail on a 65C816, 1 = half
detail on a stock 6502), so run the identical binary both ways and see where the
full-detail path crashes / hangs / diverges.

    python tools/_sim_intro6502.py --bcb 0 --frames 60     # Rapidus path
    python tools/_sim_intro6502.py --bcb 1 --frames 60     # stock path
"""
import os, sys, time, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _cpu6502 as c6

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XEX = os.path.join(PROJ, 'awintro.xex')
LST = os.path.join(PROJ, 'out', 'awintro.lst')
FRAME_CYC = 312 * 114
VB = 0xD600
MEMAC_B, MEMAC_CTL, BANK_SEL, BL_START = VB + 0x5D, VB + 0x5E, VB + 0x5F, VB + 0x53
CODE_RANGES = [(0x0600, 0x0700), (0x0700, 0x0900), (0x2000, 0x4000)]
RMW_ABS = {0x0E, 0x1E, 0x4E, 0x5E, 0x2E, 0x3E, 0x6E, 0x7E, 0xEE, 0xFE, 0xCE, 0xDE}
BOOT_INIT = 0x0706


class Sim:
    def __init__(self, bcb_h, args):
        self.args = args
        self.mem = bytearray(65536)
        self.vram = bytearray(512 * 1024)
        self.memacb = 0            # $80|bank -> $4000-$7FFF window
        self.banksel = 0           # MEMAC-A : $80|4K bank -> $8000-$8FFF
        self.L = c6.labels(LST)
        self.names = sorted((a, n) for n, a in self.L.items())
        self.addrs = [a for a, _ in self.names]
        self.cpu = c6.CPU(self.mem)
        self.cpu.rd_hook = self.rd
        self.cpu.wr_hook = self.wr
        self.spans = []            # (frame, dst, w, h, ctrl, xor)
        self.frames = 0
        self.blits = 0
        self.trace = [0] * 256; self.ti = 0
        self.bcb_h = bcb_h
        self.run_addr = None
        self.load()
        self.patch()

    # ---------------------------------------------------------------- memory
    def vaddr(self, a):
        if 0x4000 <= a < 0x8000 and self.memacb & 0x80:
            return (self.memacb & 0x7F) * 0x4000 + (a - 0x4000)
        if 0x8000 <= a < 0x9000 and self.banksel & 0x80:
            return (self.banksel & 0x7F) * 0x1000 + (a - 0x8000)
        return None

    def rd(self, a):
        v = self.vaddr(a)
        if v is not None:
            return self.vram[v]
        if a == VB + 0x40: return 0x10
        if a == VB + 0x41: return 0x20
        if a == BL_START: return 0
        if a == 0xD20F: return 0xFF          # SKSTAT: no key held
        if a == 0xD014: return 0x01          # PAL
        if a == 0xD01F: return 0x07          # CONSOL: nothing pressed
        if a == 0xD301: return 0xFF          # PORTB
        return None

    def wr(self, a, v):
        va = self.vaddr(a)
        if va is not None:
            self.vram[va] = v
            return
        if a == MEMAC_B: self.memacb = v
        elif a == BANK_SEL: self.banksel = v
        elif a == BL_START and v == 1: self.blit()

    def blit(self):
        b = self.vram[0x040100:0x040100 + 21]
        src = b[0] | (b[1] << 8) | (b[2] << 16)
        ssy = b[3] | (b[4] << 8); ssx = b[5]
        dst = b[6] | (b[7] << 8) | (b[8] << 16)
        dsy = b[9] | (b[10] << 8); dsx = b[11]
        w = (b[12] | (b[13] << 8)) + 1; h = b[14] + 1
        am, xm, ctrl = b[15], b[16], b[20]
        mode = ctrl & 7
        self.blits += 1
        self.spans.append((self.frames, dst, w, h, ctrl, xm))
        if self.args.noblit:
            return
        vr = self.vram
        if ssy > 0x7FFF: ssy -= 0x10000
        if dsy > 0x7FFF: dsy -= 0x10000
        for r in range(h):
            s = src + r * ssy; d = dst + r * dsy
            if am == 0 and ssx == 1 and dsx == 1 and mode in (0, 1, 2):
                vr[d:d + w] = bytes([xm]) * w       # solid fill fast path
                continue
            for cidx in range(w):
                sv = (vr[(s + cidx * ssx) & 0x7FFFF] & am) ^ xm
                dd = (d + cidx * dsx) & 0x7FFFF
                if mode in (0, 2): vr[dd] = sv
                elif mode == 1:
                    if sv: vr[dd] = sv
                elif mode == 3: vr[dd] |= sv
                elif mode == 4: vr[dd] &= sv
                elif mode == 5: vr[dd] ^= sv

    # ---------------------------------------------------------------- loading
    def load(self):
        data = open(XEX, 'rb').read()
        i = 0
        while i + 4 <= len(data):
            lo = data[i] | (data[i + 1] << 8); i += 2
            if lo == 0xFFFF:
                continue
            hi = data[i] | (data[i + 1] << 8); i += 2
            n = hi - lo + 1
            seg = data[i:i + n]; i += n
            if lo == 0x02E2:
                self.call(seg[0] | (seg[1] << 8))
            elif lo == 0x02E0:
                self.run_addr = seg[0] | (seg[1] << 8)
            else:
                for k in range(n):
                    a = lo + k
                    va = self.vaddr(a)
                    if va is not None:
                        self.vram[va] = seg[k]
                    else:
                        self.mem[a] = seg[k]

    def call(self, entry):
        cpu = self.cpu
        cpu.sp = 0xFD
        cpu.push(0xFF); cpu.push(0xFE)          # return to $FFFF
        cpu.pc = entry
        while cpu.pc != 0xFFFF:
            cpu.step()

    def patch(self):
        L = self.L
        for name in ('snd_settings', 'snd_init', 'snd_play', 'mus_play', 'snd_stop'):
            self.mem[L[name]] = 0x60            # rts
        # detect_cpu -> lda #bcb_h ; sta poly_bcb_h ; rts
        p = L['detect_cpu']; t = L['poly_bcb_h']
        if 'pbh_set' in L:                      # lda #bcb_h ; jmp pbh_set (+ SMC copies)
            t = L['pbh_set']
            self.mem[p:p + 5] = bytes([0xA9, self.bcb_h, 0x4C, t & 0xFF, t >> 8])
        else:
            self.mem[p:p + 6] = bytes([0xA9, self.bcb_h, 0x8D, t & 0xFF, t >> 8, 0x60])

    # ---------------------------------------------------------------- running
    def name(self, a):
        i = bisect.bisect_right(self.addrs, a) - 1
        if i < 0:
            return '$%04X' % a
        base, nm = self.names[i]
        return '%s+%d' % (nm, a - base) if a != base else nm

    def in_code(self, pc):
        for lo, hi in CODE_RANGES:
            if lo <= pc < hi:
                return True
        return False

    def run(self, frames):
        cpu, mem, L = self.cpu, self.mem, self.L
        cpu.sp = 0xFD; cpu.pc = self.run_addr; cpu.cyc = 0
        op_blit = L['op_blit']; next_op = L['next_op']; pl_byte = L['pl_byte']
        next_tick = FRAME_CYC
        steps = 0; last_prog = 0
        t0 = time.time()
        args = self.args
        try:
            while self.frames < frames:
                pc = cpu.pc
                if not self.in_code(pc):
                    raise RuntimeError('PC left the code: $%04X' % pc)
                if pc == BOOT_INIT:
                    print('intro finished (intro_done -> boot loader) after %d frames' % self.frames)
                    return True
                if pc == op_blit:
                    self.frames += 1; last_prog = steps
                    if args.verbose:
                        n = sum(1 for s in self.spans if s[0] == self.frames - 1)
                        print('  frame %4d  spans %5d  cyc %d  t=%.0fs' % (self.frames, n, cpu.cyc, time.time() - t0))
                self.trace[self.ti] = pc; self.ti = (self.ti + 1) & 255
                if args.check816:
                    op = mem[pc]
                    if op == 0x6C and mem[pc + 1] == 0xFF:
                        self.flag816('jmp ($xxFF) page-wrap', pc)
                    elif op in RMW_ABS:
                        t = mem[pc + 1] | (mem[pc + 2] << 8)
                        if op in (0x1E, 0x5E, 0x3E, 0x7E, 0xFE, 0xDE):
                            t += cpu.x
                        if 0xD000 <= t < 0xD800:
                            self.flag816('RMW on hardware $%04X (6502 double-write)' % t, pc)
                    elif op == 0x00:
                        self.flag816('BRK', pc)
                    elif op in (0xF8,):
                        self.flag816('SED (decimal mode)', pc)
                cpu.step(); steps += 1
                if cpu.cyc >= next_tick:
                    next_tick += FRAME_CYC
                    mem[0x14] = (mem[0x14] + 1) & 0xFF
                if steps - last_prog > args.hang:
                    raise RuntimeError('no BLIT for %d steps -> hang?' % args.hang)
        except RuntimeError as e:
            print('*** %s' % e)
            self.dump()
            return False
        print('ok: %d frames, %d blits, %d steps, %.0fs' % (self.frames, self.blits, steps, time.time() - t0))
        return True

    def flag816(self, what, pc):
        key = (what, pc)
        if not hasattr(self, 'seen816'):
            self.seen816 = set()
        if key not in self.seen816:
            self.seen816.add(key)
            print('  [816] %s at %s' % (what, self.name(pc)))

    def dump(self):
        cpu, mem, L = self.cpu, self.mem, self.L
        print('  frames=%d blits=%d  pc=%s a=%02X x=%02X y=%02X sp=%02X' % (
            self.frames, self.blits, self.name(cpu.pc), cpu.a, cpu.x, cpu.y, cpu.sp))
        cells = ['pl_wlo', 'pl_whi', 'pl_bnk', 'dr_off', 'dr_x', 'dr_y', 'dr_zoom', 'dr_col', 'psp',
                 'pb_ptr', 'poly_bnk', 'memb_cur', 'nverts', 'numv', 'i_idx', 'j_idx', 'row_cnt',
                 'hy_lo', 'hy_hi', 'hh', 'hgt_lo', 'hgt_hi', 'poly_bcb_h', 'sy', 'sx_lo', 'sx_hi', 'slen_lo']
        print('  ' + ' '.join('%s=%02X' % (k, mem[L[k]]) for k in cells if k in L))
        print('  last PCs (oldest first):')
        seq = [self.trace[(self.ti + k) & 255] for k in range(256)]
        out = []; prev = None
        for a in seq:
            n = self.name(a)
            if n != prev:
                out.append(n); prev = n
        print('   ' + ' > '.join(out[-60:]))
        print('  stack: ' + ' '.join('%02X' % mem[0x100 + k] for k in range(cpu.sp + 1, 0x100)))


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--bcb', type=int, default=0, help='poly_bcb_h: 0 = Rapidus/full, 1 = stock/half')
    ap.add_argument('--frames', type=int, default=40)
    ap.add_argument('--hang', type=int, default=30_000_000)
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--noblit', action='store_true')
    ap.add_argument('--check816', action='store_true', help='report 6502-vs-65C816 behaviour differences hit')
    ap.add_argument('--spans', help='write the span log to this file')
    a = ap.parse_args()
    s = Sim(a.bcb, a)
    print('loaded; run=$%04X  poly_bcb_h=%d' % (s.run_addr, a.bcb))
    ok = s.run(a.frames)
    if a.spans:
        with open(a.spans, 'w') as f:
            for sp in s.spans:
                f.write('%d %06X %d %d %02X %02X\n' % sp)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
