"""Complete documented-opcode NMOS 6502 core with cycle counting (page-cross + branch penalties)."""
import re

RW = {0xA9:('imm','lda'),0xA5:('zp','lda'),0xB5:('zpx','lda'),0xAD:('ab','lda'),0xBD:('abx','lda'),0xB9:('aby','lda'),0xA1:('inx','lda'),0xB1:('iny','lda'),
      0xA2:('imm','ldx'),0xA6:('zp','ldx'),0xB6:('zpy','ldx'),0xAE:('ab','ldx'),0xBE:('aby','ldx'),
      0xA0:('imm','ldy'),0xA4:('zp','ldy'),0xB4:('zpx','ldy'),0xAC:('ab','ldy'),0xBC:('abx','ldy'),
      0x69:('imm','adc'),0x65:('zp','adc'),0x75:('zpx','adc'),0x6D:('ab','adc'),0x7D:('abx','adc'),0x79:('aby','adc'),0x61:('inx','adc'),0x71:('iny','adc'),
      0xE9:('imm','sbc'),0xE5:('zp','sbc'),0xF5:('zpx','sbc'),0xED:('ab','sbc'),0xFD:('abx','sbc'),0xF9:('aby','sbc'),0xE1:('inx','sbc'),0xF1:('iny','sbc'),
      0x29:('imm','and'),0x25:('zp','and'),0x35:('zpx','and'),0x2D:('ab','and'),0x3D:('abx','and'),0x39:('aby','and'),0x21:('inx','and'),0x31:('iny','and'),
      0x09:('imm','ora'),0x05:('zp','ora'),0x15:('zpx','ora'),0x0D:('ab','ora'),0x1D:('abx','ora'),0x19:('aby','ora'),0x01:('inx','ora'),0x11:('iny','ora'),
      0x49:('imm','eor'),0x45:('zp','eor'),0x55:('zpx','eor'),0x4D:('ab','eor'),0x5D:('abx','eor'),0x59:('aby','eor'),0x41:('inx','eor'),0x51:('iny','eor'),
      0xC9:('imm','cmp'),0xC5:('zp','cmp'),0xD5:('zpx','cmp'),0xCD:('ab','cmp'),0xDD:('abx','cmp'),0xD9:('aby','cmp'),0xC1:('inx','cmp'),0xD1:('iny','cmp'),
      0xE0:('imm','cpx'),0xE4:('zp','cpx'),0xEC:('ab','cpx'),0xC0:('imm','cpy'),0xC4:('zp','cpy'),0xCC:('ab','cpy'),
      0x24:('zp','bit'),0x2C:('ab','bit')}
BASE = {'imm':2,'zp':3,'zpx':4,'zpy':4,'ab':4,'abx':4,'aby':4,'inx':6,'iny':5}
ST = {0x85:('zp','a'),0x95:('zpx','a'),0x8D:('ab','a'),0x9D:('abx','a'),0x99:('aby','a'),0x81:('inx','a'),0x91:('iny','a'),
      0x86:('zp','x'),0x96:('zpy','x'),0x8E:('ab','x'),0x84:('zp','y'),0x94:('zpx','y'),0x8C:('ab','y')}
STC = {'zp':3,'zpx':4,'zpy':4,'ab':4,'abx':5,'aby':5,'inx':6,'iny':6}
RMW = {0x0A:('acc','asl'),0x06:('zp','asl'),0x16:('zpx','asl'),0x0E:('ab','asl'),0x1E:('abx','asl'),
       0x4A:('acc','lsr'),0x46:('zp','lsr'),0x56:('zpx','lsr'),0x4E:('ab','lsr'),0x5E:('abx','lsr'),
       0x2A:('acc','rol'),0x26:('zp','rol'),0x36:('zpx','rol'),0x2E:('ab','rol'),0x3E:('abx','rol'),
       0x6A:('acc','ror'),0x66:('zp','ror'),0x76:('zpx','ror'),0x6E:('ab','ror'),0x7E:('abx','ror'),
       0xE6:('zp','inc'),0xF6:('zpx','inc'),0xEE:('ab','inc'),0xFE:('abx','inc'),
       0xC6:('zp','dec'),0xD6:('zpx','dec'),0xCE:('ab','dec'),0xDE:('abx','dec')}
RMWC = {'acc':2,'zp':5,'zpx':6,'ab':6,'abx':7}
MODEFN = {'inx':'inx_','iny':'iny_'}


class CPU:
    def __init__(self, mem):
        self.m = mem; self.a = self.x = self.y = 0; self.sp = 0xFD; self.pc = 0
        self.c = self.z = self.n = self.v = self.d = self.i = 0; self.cyc = 0
        self.wr_hook = None; self.rd_hook = None

    def rd(self, a):
        a &= 0xFFFF
        if self.rd_hook:
            r = self.rd_hook(a)
            if r is not None:
                return r
        return self.m[a]

    def wr(self, a, v):
        a &= 0xFFFF; v &= 0xFF; self.m[a] = v
        if self.wr_hook:
            self.wr_hook(a, v)

    def push(self, v): self.wr(0x100 + self.sp, v); self.sp = (self.sp - 1) & 0xFF
    def pop(self): self.sp = (self.sp + 1) & 0xFF; return self.rd(0x100 + self.sp)
    def nz(self, v): v &= 0xFF; self.z = int(v == 0); self.n = v >> 7; return v
    def flags(self):
        return (self.n << 7) | (self.v << 6) | 0x20 | (self.d << 3) | (self.i << 2) | (self.z << 1) | self.c
    def setflags(self, p):
        self.n = p >> 7 & 1; self.v = p >> 6 & 1; self.d = p >> 3 & 1; self.i = p >> 2 & 1; self.z = p >> 1 & 1; self.c = p & 1

    def imm(self): v = self.rd(self.pc); self.pc = (self.pc + 1) & 0xFFFF; return v
    def zp(self): return self.imm(), 0
    def zpx(self): return (self.imm() + self.x) & 0xFF, 0
    def zpy(self): return (self.imm() + self.y) & 0xFF, 0
    def ab(self): lo = self.imm(); hi = self.imm(); return lo | (hi << 8), 0
    def abx(self): b = self.ab()[0]; a = (b + self.x) & 0xFFFF; return a, int((b ^ a) & 0xFF00 != 0)
    def aby(self): b = self.ab()[0]; a = (b + self.y) & 0xFFFF; return a, int((b ^ a) & 0xFF00 != 0)
    def inx_(self):
        z = (self.imm() + self.x) & 0xFF; return self.m[z] | (self.m[(z + 1) & 0xFF] << 8), 0
    def iny_(self):
        z = self.imm(); b = self.m[z] | (self.m[(z + 1) & 0xFF] << 8); a = (b + self.y) & 0xFFFF
        return a, int((b ^ a) & 0xFF00 != 0)

    def adc(self, v):
        if self.d:
            raise RuntimeError('decimal mode')
        s = self.a + v + self.c; r = s & 0xFF
        self.v = int((~(self.a ^ v) & (self.a ^ r)) & 0x80 != 0); self.c = int(s > 0xFF); self.a = self.nz(r)
    def sbc(self, v): self.adc(v ^ 0xFF)
    def cmp_(self, r, v): self.c = int(r >= v); self.nz((r - v) & 0xFF)
    def branch(self, take):
        off = self.imm(); self.cyc += 2
        if take:
            t = (self.pc + (off - 256 if off > 127 else off)) & 0xFFFF
            self.cyc += 1 + int((t ^ self.pc) & 0xFF00 != 0); self.pc = t

    def step(self):
        op = self.imm()
        if op in RW:
            mode, name = RW[op]
            if mode == 'imm':
                v = self.imm(); pc = 0
            else:
                a, pc = getattr(self, MODEFN.get(mode, mode))(); v = self.rd(a)
            self.cyc += BASE[mode] + pc
            if name == 'lda': self.a = self.nz(v)
            elif name == 'ldx': self.x = self.nz(v)
            elif name == 'ldy': self.y = self.nz(v)
            elif name == 'adc': self.adc(v)
            elif name == 'sbc': self.sbc(v)
            elif name == 'and': self.a = self.nz(self.a & v)
            elif name == 'ora': self.a = self.nz(self.a | v)
            elif name == 'eor': self.a = self.nz(self.a ^ v)
            elif name == 'cmp': self.cmp_(self.a, v)
            elif name == 'cpx': self.cmp_(self.x, v)
            elif name == 'cpy': self.cmp_(self.y, v)
            elif name == 'bit': self.z = int((self.a & v) == 0); self.n = v >> 7; self.v = v >> 6 & 1
            return
        if op in ST:
            mode, r = ST[op]; a, _ = getattr(self, MODEFN.get(mode, mode))()
            self.cyc += STC[mode]; self.wr(a, getattr(self, r)); return
        if op in RMW:
            mode, name = RMW[op]
            if mode == 'acc':
                v = self.a; a = None
            else:
                a, _ = getattr(self, mode)(); v = self.rd(a)
            self.cyc += RMWC[mode]
            if name == 'asl': self.c = v >> 7; v = self.nz(v << 1)
            elif name == 'lsr': self.c = v & 1; v = self.nz(v >> 1)
            elif name == 'rol': c = self.c; self.c = v >> 7; v = self.nz((v << 1) | c)
            elif name == 'ror': c = self.c; self.c = v & 1; v = self.nz((v >> 1) | (c << 7))
            elif name == 'inc': v = self.nz(v + 1)
            elif name == 'dec': v = self.nz(v - 1)
            if a is None: self.a = v
            else: self.wr(a, v)
            return
        if op == 0x10: self.branch(not self.n)
        elif op == 0x30: self.branch(self.n)
        elif op == 0x50: self.branch(not self.v)
        elif op == 0x70: self.branch(self.v)
        elif op == 0x90: self.branch(not self.c)
        elif op == 0xB0: self.branch(self.c)
        elif op == 0xD0: self.branch(not self.z)
        elif op == 0xF0: self.branch(self.z)
        elif op == 0x4C: self.pc = self.ab()[0]; self.cyc += 3
        elif op == 0x6C:
            p = self.ab()[0]; lo = self.rd(p); hi = self.rd((p & 0xFF00) | ((p + 1) & 0xFF)); self.pc = lo | (hi << 8); self.cyc += 5
        elif op == 0x20:
            t = self.ab()[0]; r = (self.pc - 1) & 0xFFFF; self.push(r >> 8); self.push(r & 0xFF); self.pc = t; self.cyc += 6
        elif op == 0x60: lo = self.pop(); hi = self.pop(); self.pc = (((hi << 8) | lo) + 1) & 0xFFFF; self.cyc += 6
        elif op == 0x40: self.setflags(self.pop()); lo = self.pop(); hi = self.pop(); self.pc = (hi << 8) | lo; self.cyc += 6
        elif op == 0x48: self.push(self.a); self.cyc += 3
        elif op == 0x68: self.a = self.nz(self.pop()); self.cyc += 4
        elif op == 0x08: self.push(self.flags() | 0x10); self.cyc += 3
        elif op == 0x28: self.setflags(self.pop()); self.cyc += 4
        elif op == 0xAA: self.x = self.nz(self.a); self.cyc += 2
        elif op == 0x8A: self.a = self.nz(self.x); self.cyc += 2
        elif op == 0xA8: self.y = self.nz(self.a); self.cyc += 2
        elif op == 0x98: self.a = self.nz(self.y); self.cyc += 2
        elif op == 0xBA: self.x = self.nz(self.sp); self.cyc += 2
        elif op == 0x9A: self.sp = self.x; self.cyc += 2
        elif op == 0xE8: self.x = self.nz(self.x + 1); self.cyc += 2
        elif op == 0xCA: self.x = self.nz(self.x - 1); self.cyc += 2
        elif op == 0xC8: self.y = self.nz(self.y + 1); self.cyc += 2
        elif op == 0x88: self.y = self.nz(self.y - 1); self.cyc += 2
        elif op == 0x18: self.c = 0; self.cyc += 2
        elif op == 0x38: self.c = 1; self.cyc += 2
        elif op == 0x58: self.i = 0; self.cyc += 2
        elif op == 0x78: self.i = 1; self.cyc += 2
        elif op == 0xB8: self.v = 0; self.cyc += 2
        elif op == 0xD8: self.d = 0; self.cyc += 2
        elif op == 0xF8: self.d = 1; self.cyc += 2
        elif op == 0xEA: self.cyc += 2
        elif op == 0x00: raise RuntimeError('BRK at $%04X' % (self.pc - 1))
        else: raise RuntimeError('unimplemented opcode $%02X at $%04X' % (op, self.pc - 1))


def load_xex(mem, data):
    """Load an Atari XEX into mem; returns list of (start,end) segments."""
    segs = []; i = 0
    while i + 4 <= len(data):
        lo = data[i] | (data[i + 1] << 8); i += 2
        if lo == 0xFFFF:
            continue
        hi = data[i] | (data[i + 1] << 8); i += 2
        n = hi - lo + 1; mem[lo:lo + n] = data[i:i + n]; i += n; segs.append((lo, hi))
    return segs


def labels(lst_path):
    """name -> address from a MADS listing (.proc, equ/=, plain labels)."""
    d = {}
    for ln in open(lst_path, encoding='utf-8', errors='replace'):
        m = re.match(r'^\s*\d+\s+(?:=\s*)?([0-9A-F]{4})(?:-[0-9A-F]{4}>)?(?:\s[0-9A-F]{2})*\t+(\S.*)$', ln)
        if not m:
            continue
        addr = int(m.group(1), 16); src = m.group(2).rstrip()
        if src.startswith(';'):
            continue
        mm = re.match(r'^\.proc\s+([A-Za-z_]\w*)', src)
        if mm:
            d[mm.group(1)] = addr; continue
        mm = re.match(r'^([A-Za-z_]\w*)\b', src)
        if mm:
            name = mm.group(1)
            if name not in d:
                d[name] = addr
    return d
