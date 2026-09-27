#!/usr/bin/env python3
"""sim.py - run gr7/awgr7.xex on a cycle-counting 6502 with 320K-style banking.

What it IS good for:
  * screenshots without an emulator (decodes the real display list + colour shadows)
  * cycles per frame, and a profile of where they go
  * assertions about program state (e.g. "does copy_page really copy the page")

What it is NOT:
  * no ANTIC/GTIA timing, no DLI, no interrupts, no SIO, no POKEY. It executes the
    CPU and models memory; everything else is ignored. It found real bugs (a
    6-byte-per-row copy_page, code executing display-list bytes) but it will never
    settle a question about raster timing -- for that, Altirra is the authority.

    python gr7/sim.py                       200 frames, timing + profile
    python gr7/sim.py 400 --sheet 20,60,120 contact sheet of those frames
    python gr7/sim.py --check-copy          verify copy_page leaves dst == src
"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
XEX = os.path.join(HERE, 'awgr7.xex')
LST = os.path.join(HERE, 'out', 'gr7.lst')

FRAME_CYC = 312 * 114                   # PAL
DMA_D = 100 * 40 + 312 * 9              # ANTIC D: 40 B per 2-scanline row + refresh
BUDGET = FRAME_CYC - DMA_D

CYC = {
    0x69:2,0x65:3,0x75:4,0x6D:4,0x7D:4,0x79:4,0x61:6,0x71:5,
    0x29:2,0x25:3,0x35:4,0x2D:4,0x3D:4,0x39:4,0x21:6,0x31:5,
    0x0A:2,0x06:5,0x16:6,0x0E:6,0x1E:7, 0x24:3,0x2C:4,
    0x18:2,0x38:2,0x58:2,0x78:2,0xB8:2,0xD8:2,0xF8:2,
    0xC9:2,0xC5:3,0xD5:4,0xCD:4,0xDD:4,0xD9:4,0xC1:6,0xD1:5,
    0xE0:2,0xE4:3,0xEC:4,0xC0:2,0xC4:3,0xCC:4,
    0xC6:5,0xD6:6,0xCE:6,0xDE:7,0xCA:2,0x88:2,
    0x49:2,0x45:3,0x55:4,0x4D:4,0x5D:4,0x59:4,0x41:6,0x51:5,
    0xE6:5,0xF6:6,0xEE:6,0xFE:7,0xE8:2,0xC8:2,
    0x4C:3,0x6C:5,0x20:6,0x60:6,0x40:6,
    0xA9:2,0xA5:3,0xB5:4,0xAD:4,0xBD:4,0xB9:4,0xA1:6,0xB1:5,
    0xA2:2,0xA6:3,0xB6:4,0xAE:4,0xBE:4, 0xA0:2,0xA4:3,0xB4:4,0xAC:4,0xBC:4,
    0x4A:2,0x46:5,0x56:6,0x4E:6,0x5E:7, 0xEA:2,
    0x09:2,0x05:3,0x15:4,0x0D:4,0x1D:4,0x19:4,0x01:6,0x11:5,
    0x48:3,0x08:3,0x68:4,0x28:4,
    0x2A:2,0x26:5,0x36:6,0x2E:6,0x3E:7, 0x6A:2,0x66:5,0x76:6,0x6E:6,0x7E:7,
    0xE9:2,0xE5:3,0xF5:4,0xED:4,0xFD:4,0xF9:4,0xE1:6,0xF1:5,
    0x85:3,0x95:4,0x8D:4,0x9D:5,0x99:5,0x81:6,0x91:6,
    0x86:3,0x96:4,0x8E:4,0x84:3,0x94:4,0x8C:4,
    0xAA:2,0xA8:2,0xBA:2,0x8A:2,0x9A:2,0x98:2,
    0x10:2,0x30:2,0x50:2,0x70:2,0x90:2,0xB0:2,0xD0:2,0xF0:2,
}
BRANCH = {0x10:('n',0),0x30:('n',1),0x50:('v',0),0x70:('v',1),
          0x90:('c',0),0xB0:('c',1),0xD0:('z',0),0xF0:('z',1)}
RMW = {0x06:'zp',0x16:'zpx',0x0E:'ab',0x1E:'abx',0x46:'zp',0x56:'zpx',0x4E:'ab',
       0x5E:'abx',0x26:'zp',0x36:'zpx',0x2E:'ab',0x3E:'abx',0x66:'zp',0x76:'zpx',
       0x6E:'ab',0x7E:'abx',0xE6:'zp',0xF6:'zpx',0xEE:'ab',0xFE:'abx',
       0xC6:'zp',0xD6:'zpx',0xCE:'ab',0xDE:'abx'}


class Mem:
    """64K base + 16 x 16K extended, banked into $4000-$7FFF by PORTB.
    320K Rambo decode (Altirra mmu.cpp): bit4=0 enables, bits 2/3/5/6 are the bank,
    active low."""
    def __init__(self):
        self.base = bytearray(0x10000)
        self.ext = [bytearray(0x4000) for _ in range(16)]
        self.portb = 0xFF
        self.bank = None

    def set_portb(self, v):
        self.portb = v
        self.bank = None if (v & 0x10) else (((~v & 0x0C) >> 2) | ((~v & 0x60) >> 3))

    def rd(self, a):
        a &= 0xFFFF
        if 0x4000 <= a < 0x8000 and self.bank is not None:
            return self.ext[self.bank][a - 0x4000]
        if a == 0xD301:
            return self.portb
        return self.base[a]

    def wr(self, a, v):
        a &= 0xFFFF; v &= 0xFF
        if a == 0xD301:
            self.set_portb(v); return
        if 0xD000 <= a < 0xD800:
            return
        if 0x4000 <= a < 0x8000 and self.bank is not None:
            self.ext[self.bank][a - 0x4000] = v; return
        self.base[a] = v


class CPU:
    def __init__(self, m):
        self.m = m; self.a = self.x = self.y = 0; self.sp = 0xFD; self.pc = 0
        self.c = self.z = self.n = self.v = self.d = self.i = 0; self.cyc = 0

    def _fb(self):
        v = self.m.rd(self.pc); self.pc = (self.pc + 1) & 0xFFFF; return v
    def _fw(self):
        return self._fb() | (self._fb() << 8)
    def _ad(self, mode, write=False):
        if mode == 'imm': return None
        if mode == 'zp':  return self._fb()
        if mode == 'zpx': return (self._fb() + self.x) & 0xFF
        if mode == 'zpy': return (self._fb() + self.y) & 0xFF
        if mode == 'ab':  return self._fw()
        if mode in ('abx', 'aby'):
            b = self._fw(); r = self.x if mode == 'abx' else self.y
            a = (b + r) & 0xFFFF
            if not write and (b & 0xFF00) != (a & 0xFF00): self.cyc += 1
            return a
        if mode == 'ix':
            p = (self._fb() + self.x) & 0xFF
            return self.m.rd(p) | (self.m.rd((p + 1) & 0xFF) << 8)
        p = self._fb()
        b = self.m.rd(p) | (self.m.rd((p + 1) & 0xFF) << 8)
        a = (b + self.y) & 0xFFFF
        if not write and (b & 0xFF00) != (a & 0xFF00): self.cyc += 1
        return a

    def _nz(self, v):
        v &= 0xFF; self.z = int(v == 0); self.n = (v >> 7) & 1; return v
    def _push(self, v): self.m.wr(0x100 + self.sp, v); self.sp = (self.sp - 1) & 0xFF
    def _pop(self): self.sp = (self.sp + 1) & 0xFF; return self.m.rd(0x100 + self.sp)
    def _p(self):
        return (self.n<<7)|(self.v<<6)|0x30|(self.d<<3)|(self.i<<2)|(self.z<<1)|self.c
    def _setp(self, p):
        self.n=(p>>7)&1; self.v=(p>>6)&1; self.d=(p>>3)&1
        self.i=(p>>2)&1; self.z=(p>>1)&1; self.c=p&1
    def _adc(self, v):
        t = self.a + v + self.c
        self.v = (~(self.a ^ v) & (self.a ^ t) & 0x80) >> 7
        self.c = int(t > 0xFF); self.a = self._nz(t)
    def _cmp(self, r, v):
        self.c = int(r >= v); self._nz((r - v) & 0xFF)

    GRP = {
        'lda': {0xA9:'imm',0xA5:'zp',0xB5:'zpx',0xAD:'ab',0xBD:'abx',0xB9:'aby',0xA1:'ix',0xB1:'iy'},
        'ldx': {0xA2:'imm',0xA6:'zp',0xB6:'zpy',0xAE:'ab',0xBE:'aby'},
        'ldy': {0xA0:'imm',0xA4:'zp',0xB4:'zpx',0xAC:'ab',0xBC:'abx'},
        'sta': {0x85:'zp',0x95:'zpx',0x8D:'ab',0x9D:'abx',0x99:'aby',0x81:'ix',0x91:'iy'},
        'stx': {0x86:'zp',0x96:'zpy',0x8E:'ab'},
        'sty': {0x84:'zp',0x94:'zpx',0x8C:'ab'},
        'and': {0x29:'imm',0x25:'zp',0x35:'zpx',0x2D:'ab',0x3D:'abx',0x39:'aby',0x21:'ix',0x31:'iy'},
        'ora': {0x09:'imm',0x05:'zp',0x15:'zpx',0x0D:'ab',0x1D:'abx',0x19:'aby',0x01:'ix',0x11:'iy'},
        'eor': {0x49:'imm',0x45:'zp',0x55:'zpx',0x4D:'ab',0x5D:'abx',0x59:'aby',0x41:'ix',0x51:'iy'},
        'adc': {0x69:'imm',0x65:'zp',0x75:'zpx',0x6D:'ab',0x7D:'abx',0x79:'aby',0x61:'ix',0x71:'iy'},
        'sbc': {0xE9:'imm',0xE5:'zp',0xF5:'zpx',0xED:'ab',0xFD:'abx',0xF9:'aby',0xE1:'ix',0xF1:'iy'},
        'cmp': {0xC9:'imm',0xC5:'zp',0xD5:'zpx',0xCD:'ab',0xDD:'abx',0xD9:'aby',0xC1:'ix',0xD1:'iy'},
        'cpx': {0xE0:'imm',0xE4:'zp',0xEC:'ab'},
        'cpy': {0xC0:'imm',0xC4:'zp',0xCC:'ab'},
        'bit': {0x24:'zp',0x2C:'ab'},
    }
    OPMAP = {}
    for _k, _v in GRP.items():
        for _op, _md in _v.items():
            OPMAP[_op] = (_k, _md)

    def step(self):
        op = self._fb()
        c = CYC.get(op)
        if c is None:
            raise RuntimeError('opcode $%02X @ $%04X' % (op, (self.pc - 1) & 0xFFFF))
        self.cyc += c
        m = self.m

        if op in BRANCH:
            f, want = BRANCH[op]; off = self._fb()
            if getattr(self, f) == want:
                t = (self.pc + (off - 256 if off > 127 else off)) & 0xFFFF
                self.cyc += 2 if (t & 0xFF00) != (self.pc & 0xFF00) else 1
                self.pc = t
            return

        if op in RMW:
            a = self._ad(RMW[op], True); v = m.rd(a)
            if   op in (0x06,0x16,0x0E,0x1E): self.c = (v >> 7) & 1; v <<= 1
            elif op in (0x46,0x56,0x4E,0x5E): self.c = v & 1; v >>= 1
            elif op in (0x26,0x36,0x2E,0x3E): t = (v << 1) | self.c; self.c = (v >> 7) & 1; v = t
            elif op in (0x66,0x76,0x6E,0x7E): t = (v >> 1) | (self.c << 7); self.c = v & 1; v = t
            elif op in (0xE6,0xF6,0xEE,0xFE): v += 1
            else: v -= 1
            m.wr(a, self._nz(v)); return

        if op in self.OPMAP:
            kind, mode = self.OPMAP[op]
            if kind in ('sta', 'stx', 'sty'):
                a = self._ad(mode, True)
                m.wr(a, {'sta': self.a, 'stx': self.x, 'sty': self.y}[kind]); return
            a = self._ad(mode)
            v = self._fb() if mode == 'imm' else m.rd(a)
            if   kind == 'lda': self.a = self._nz(v)
            elif kind == 'ldx': self.x = self._nz(v)
            elif kind == 'ldy': self.y = self._nz(v)
            elif kind == 'and': self.a = self._nz(self.a & v)
            elif kind == 'ora': self.a = self._nz(self.a | v)
            elif kind == 'eor': self.a = self._nz(self.a ^ v)
            elif kind == 'adc': self._adc(v)
            elif kind == 'sbc': self._adc(v ^ 0xFF)
            elif kind == 'cmp': self._cmp(self.a, v)
            elif kind == 'cpx': self._cmp(self.x, v)
            elif kind == 'cpy': self._cmp(self.y, v)
            else:
                self.z = int((self.a & v) == 0); self.n = (v >> 7) & 1; self.v = (v >> 6) & 1
            return

        if   op == 0xAA: self.x = self._nz(self.a)
        elif op == 0xA8: self.y = self._nz(self.a)
        elif op == 0xBA: self.x = self._nz(self.sp)
        elif op == 0x8A: self.a = self._nz(self.x)
        elif op == 0x9A: self.sp = self.x
        elif op == 0x98: self.a = self._nz(self.y)
        elif op == 0xE8: self.x = self._nz(self.x + 1)
        elif op == 0xC8: self.y = self._nz(self.y + 1)
        elif op == 0xCA: self.x = self._nz(self.x - 1)
        elif op == 0x88: self.y = self._nz(self.y - 1)
        elif op == 0x48: self._push(self.a)
        elif op == 0x68: self.a = self._nz(self._pop())
        elif op == 0x08: self._push(self._p())
        elif op == 0x28: self._setp(self._pop())
        elif op == 0x0A: self.c = (self.a >> 7) & 1; self.a = self._nz(self.a << 1)
        elif op == 0x4A: self.c = self.a & 1; self.a = self._nz(self.a >> 1)
        elif op == 0x2A: t = (self.a << 1) | self.c; self.c = (self.a >> 7) & 1; self.a = self._nz(t)
        elif op == 0x6A: t = (self.a >> 1) | (self.c << 7); self.c = self.a & 1; self.a = self._nz(t)
        elif op == 0x18: self.c = 0
        elif op == 0x38: self.c = 1
        elif op == 0x58: self.i = 0
        elif op == 0x78: self.i = 1
        elif op == 0xB8: self.v = 0
        elif op == 0xD8: self.d = 0
        elif op == 0xF8: self.d = 1
        elif op == 0xEA: pass
        elif op == 0x4C: self.pc = self._fw()
        elif op == 0x6C:
            p = self._fw()
            self.pc = m.rd(p) | (m.rd((p & 0xFF00) | ((p + 1) & 0xFF)) << 8)
        elif op == 0x20:
            t = self._fw(); r = (self.pc - 1) & 0xFFFF
            self._push(r >> 8); self._push(r & 0xFF); self.pc = t
        elif op == 0x60:
            lo = self._pop(); self.pc = ((self._pop() << 8) | lo) + 1 & 0xFFFF
        elif op == 0x40:
            self._setp(self._pop()); lo = self._pop(); self.pc = lo | (self._pop() << 8)
        else:
            raise RuntimeError('unhandled $%02X' % op)


def load(mem, cpu):
    d = open(XEX, 'rb').read(); i = 2; run = None
    while i + 3 < len(d):
        a = d[i] | (d[i+1] << 8); e = d[i+2] | (d[i+3] << 8); i += 4
        if a == 0xFFFF: i -= 2; continue
        n = e - a + 1; blob = d[i:i+n]; i += n
        if a == 0x02E2:                                 # INITAD: run it now
            cpu.pc = blob[0] | (blob[1] << 8)
            cpu._push(0x99); cpu._push(0x99); sp0 = cpu.sp
            for _ in range(10000):
                if cpu.sp > sp0: break
                cpu.step()
            continue
        if a == 0x02E0:
            run = blob[0] | (blob[1] << 8); continue
        for k, b in enumerate(blob):
            mem.wr(a + k, b)
    return run


def symbols():
    out = {}
    for ln in open(LST, encoding='latin-1'):
        m = re.match(r'^\s*\d+\s+([0-9A-F]{4})\s+.*?\t\s*\.proc\s+(\w+)', ln)
        if not m:
            m = re.match(r'^\s*\d+\s+([0-9A-F]{4})\s+[0-9A-F ]*\t([A-Za-z_]\w*)\s', ln)
        if m: out.setdefault(int(m.group(1), 16), m.group(2))
    return sorted(out.items())


def shot(mem):
    """Decode what ANTIC would be showing: LMS page from the display list, and the
    four colour-register shadows the VBI copies into GTIA."""
    import numpy as np
    sys.path.insert(0, HERE)
    from atari_pal import PALETTE
    dl = 0x2D00
    for ln in open(os.path.join(HERE, 'gr7.inc'), encoding='utf-8'):
        m = re.match(r'^DLIST\s+equ\s+\$([0-9A-Fa-f]+)', ln)
        if m: dl = int(m.group(1), 16)
    base = mem.base[dl + 3] | (mem.base[dl + 4] << 8)
    regs = [mem.base[0x02C8], mem.base[0x02C4], mem.base[0x02C5], mem.base[0x02C6]]
    px = np.zeros((100, 160), np.uint8)
    for r in range(100):
        b = np.frombuffer(bytes(mem.base[base + r*40: base + r*40 + 40]), np.uint8)
        for k in range(4):
            px[r, k::4] = (b >> (6 - 2*k)) & 3
    rgb = PALETTE[np.array(regs, np.uint8)][px]
    return np.repeat(np.repeat(rgb, 2, 1), 2, 0), base, regs


def main():
    import bisect, collections, statistics
    args = sys.argv[1:]
    nframes = next((int(a) for a in args if a.isdigit()), 200)
    sheet = None
    if '--sheet' in args:
        sheet = set(int(v) for v in args[args.index('--sheet') + 1].split(','))
    check_copy = '--check-copy' in args
    cmp_at = None
    if '--cmp' in args:
        cmp_at = sorted(int(v) for v in args[args.index('--cmp') + 1].split(','))
        sheet = set(cmp_at)

    mem = Mem(); cpu = CPU(mem); mem.set_portb(0xFF)
    run = load(mem, cpu)
    syms = symbols()
    addrs = [a for a, _ in syms]; names = [n for _, n in syms]
    by_name = {n: a for a, n in syms}
    print('RUN $%04X, bank s datami: %d' % (run, sum(1 for b in mem.ext if any(b))))

    cpu.pc = run; cpu._push(0x99); cpu._push(0x99)
    BLIT = by_name.get('op_blit')
    WV = by_name.get('wait_vblank')
    wv_end = next((a for a, _ in syms if WV and a > WV), 0xFFFF)
    CP = by_name.get('copy_page')

    prof = collections.Counter()
    frames, grabs = [], []
    vbl = FRAME_CYC; last = idle_f = steps = 0
    cp_sp = None; cp_ok = cp_bad = 0
    try:
        while len(frames) < nframes:
            if cpu.pc == BLIT:
                if sheet is not None and len(frames) in sheet:
                    grabs.append((len(frames), shot(mem)[0]))
                frames.append(cpu.cyc - last - idle_f); last = cpu.cyc; idle_f = 0
            if check_copy and CP:
                if cpu.pc == CP and cp_sp is None:
                    cp_sp = cpu.sp; src = mem.base[0x9E]; dst = mem.base[0x9F]
                elif cp_sp is not None and cpu.sp > cp_sp:
                    sb = 0x8000 + (src & 3) * 0x1000; db = 0x8000 + (dst & 3) * 0x1000
                    if mem.base[sb:sb+4000] == mem.base[db:db+4000]: cp_ok += 1
                    else: cp_bad += 1
                    cp_sp = None
            pc0, c0 = cpu.pc, cpu.cyc
            cpu.step()
            dc = cpu.cyc - c0
            if WV and WV <= pc0 < wv_end: idle_f += dc
            else:
                i = bisect.bisect_right(addrs, pc0) - 1
                if i >= 0: prof[names[i]] += dc
            steps += 1
            if cpu.cyc >= vbl:
                vbl += FRAME_CYC; mem.base[0x14] = (mem.base[0x14] + 1) & 0xFF
    except RuntimeError as e:
        print('STOP:', e, '(po %d snimkach)' % len(frames))

    if check_copy:
        print('copy_page: %d OK, %d chybnych' % (cp_ok, cp_bad))

    if cmp_at and grabs:
        # 1:1 check against the project's own oracle (tools/aw_sim.py): same frame,
        # same 16->4 tables, same reduction to 160x100. Anything left is a bug in
        # the 6502 code or a semantic the port does differently ($10 transparent and
        # $11+ page-0 copy cannot be reproduced exactly with four colours).
        import numpy as np
        sys.path.insert(0, os.path.join(PROJ, 'tools'))
        from atari_pal import PALETTE
        import aw_sim
        lutb = open(os.path.join(HERE, 'out', 'gr7_pallut16.bin'), 'rb').read()
        regb = open(os.path.join(HERE, 'out', 'gr7_palreg.bin'), 'rb').read()
        oframes, _ = aw_sim.render_intro(max(cmp_at) + 2, 'int')
        pairs = []
        print()
        print('snimka   rozdiel voci oracle')
        for fn, got in grabs:
            if fn - 1 >= len(oframes):
                continue
            page, pal, hold, draws, dl = oframes[fn - 1]
            a = np.frombuffer(bytes(page), np.uint8).reshape(200, 320)[::2, ::2]
            codes = np.array([lutb[pal*16 + v] for v in range(16)], np.uint8)[a]
            ref = PALETTE[np.frombuffer(regb[pal*4:pal*4+4], np.uint8)][codes]
            ref = np.repeat(np.repeat(ref, 2, 1), 2, 0)
            # what the VBXE build shows: 160x200, the 16 AW colours exactly
            awp = open(os.path.join(PROJ, 'out', 'intro_pal.bin'), 'rb').read()
            aw = np.array([[awp[pal*48+j*3]<<1, awp[pal*48+j*3+1]<<1, awp[pal*48+j*3+2]<<1]
                           for j in range(16)], np.uint8)
            full = np.frombuffer(bytes(page), np.uint8).reshape(200, 320)[:, ::2]
            vbxe = np.repeat(aw[full], 2, 1)
            h = min(ref.shape[0], got.shape[0])
            diff = (np.abs(ref[:h].astype(int) - got[:h].astype(int)).sum(2) > 0).mean() * 100
            print('  %5d   %5.1f %%   (AW paleta %d, %d polygonov)' % (fn, diff, pal, draws))
            # which SCANLINE do they first disagree on? A clean horizontal cut
            # means the raster stopped part way down a polygon, not that a colour
            # is wrong.
            # per-row difference. Scattered single pixels are edge rounding; a run
            # of rows that differ almost completely means the raster stopped part
            # way down, which is a different (and much worse) kind of bug.
            per = (np.abs(ref[:h].astype(int) - got[:h].astype(int)).sum(2) > 0)
            per = per.reshape(h // 2, 2, -1).mean(axis=(1, 2)) * 100
            bad = np.where(per > 80)[0]
            if len(bad):
                print('           riadky s >80%% rozdielom: %d..%d (%d z %d)'
                      % (bad[0], bad[-1], len(bad), h // 2))
            else:
                print('           ziadny riadok nie je rozbity >80%%, max %.0f%%' % per.max())
            pairs.append((fn, vbxe, ref[:h], got[:h], diff))
        if pairs:
            from PIL import Image, ImageDraw
            th, tw = pairs[0][2].shape[:2]
            vh = pairs[0][1].shape[0]
            rowh = max(th, vh)
            im = Image.new('RGB', (3*tw+32, len(pairs)*(rowh+22)+8), (24, 24, 28))
            dr = ImageDraw.Draw(im)
            for i, (fn, vb, r0, g0, df) in enumerate(pairs):
                y = 8 + i*(rowh+22)
                im.paste(Image.fromarray(vb), (8, y))
                im.paste(Image.fromarray(r0), (tw+16, y))
                im.paste(Image.fromarray(g0), (2*tw+24, y))
                dr.text((8, y+rowh+4), 'f%d  VBXE 160x200 16 farieb' % fn, (170, 200, 255))
                dr.text((tw+16, y+rowh+4), 'ciel pre GR.7 (4 farby)', (255, 210, 120))
                dr.text((2*tw+24, y+rowh+4), 'skutocny xex   %.1f %%' % df, (150, 230, 170))
            im.save(os.path.join(HERE, 'out', 'cmp.png'))
            print('-> gr7/out/cmp.png')

    if grabs:
        import numpy as np
        from PIL import Image, ImageDraw
        cols = 3; rows = (len(grabs) + cols - 1) // cols
        th, tw = grabs[0][1].shape[:2]
        img = Image.new('RGB', (cols*(tw+8)+8, rows*(th+22)+8), (24, 24, 28))
        d = ImageDraw.Draw(img)
        for i, (fn, a) in enumerate(grabs):
            r, c = divmod(i, cols); x, y = 8+c*(tw+8), 8+r*(th+22)
            img.paste(Image.fromarray(a), (x, y))
            d.text((x, y+th+4), 'frame %d' % fn, (200, 200, 210))
        img.save(os.path.join(HERE, 'out', 'sheet.png'))
        print('-> gr7/out/sheet.png')

    a, base, regs = shot(mem)
    from PIL import Image
    Image.fromarray(a).save(os.path.join(HERE, 'out', 'shot.png'))
    print('-> gr7/out/shot.png  (LMS $%04X, registre %s)'
          % (base, ' '.join('$%02X' % r for r in regs)))

    fr = frames[1:] or frames
    if fr:
        med = statistics.median(fr)
        print('\nsnimok %d   cyklov: min %d  median %.0f  max %d'
              % (len(fr), min(fr), med, max(fr)))
        print('PAL rozpocet s ANTIC D DMA: %d cyk/snimka' % BUDGET)
        print('median = %.2f PAL snimky -> %.1f fps' % (med/BUDGET, 50/max(1, med/BUDGET)))
        tot = sum(prof.values())
        print('\nkam ide cas (bez cakania na vblank):')
        for n, c in prof.most_common(10):
            print('   %5.1f %%  %s' % (c*100/tot, n))


if __name__ == '__main__':
    main()
