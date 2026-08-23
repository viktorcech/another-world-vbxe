#!/usr/bin/env python3
"""sim_boot6502.py - run the REAL assembled boot loader (out/boot.bin) on a tiny
6502 core over the real awgame_full.atr.

sim_boot.py re-implements the loader in Python; this one executes the actual
opcodes mads produced, so it also catches assembly-level mistakes that a
re-implementation cannot -- e.g. the `?ok` local-label collision that made
get_byte branch into read_sec (bootloader.asm has no .proc, so every `?` label
shares one scope).

SIOV ($E459) is hooked: it serves the sector named by DAUX1/2 from the ATR into
DBUFLO/HI and returns status $01 in Y.  --fail lets a sector return an error
status instead, which is what a real SIO2SD can do and a SIDE3 never does.

    python tools/sim_boot6502.py                 # boot the disk, verify the load
    python tools/sim_boot6502.py --fail 1234:2   # sector 1234 fails twice, then works
    python tools/sim_boot6502.py --fail 1234:99  # sector 1234 always fails
"""
import os, sys, struct

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ATR = os.path.join(PROJ, 'awgame_full.atr')
BOOT = os.path.join(PROJ, 'out', 'boot.bin')
SECTOR = 128
LOADER_LO, LOADER_HI = 0x0700, 0x08FF          # loader code + its sector buffer
SIOV = 0xE459


class CPU:
    def __init__(self, mem):
        self.m = mem
        self.a = self.x = self.y = 0
        self.sp = 0xFD
        self.pc = 0
        self.c = self.z = self.n = self.v = 0

    # --- helpers ---------------------------------------------------------
    def rd(self, a):  return self.m[a & 0xFFFF]
    def wr(self, a, v): self.m[a & 0xFFFF] = v & 0xFF
    def imm(self):
        v = self.rd(self.pc); self.pc += 1; return v
    def abs_(self):
        lo = self.imm(); hi = self.imm(); return lo | (hi << 8)
    def nz(self, v):
        v &= 0xFF; self.z = int(v == 0); self.n = (v >> 7) & 1; return v
    def push(self, v):
        self.wr(0x0100 + self.sp, v); self.sp = (self.sp - 1) & 0xFF
    def pop(self):
        self.sp = (self.sp + 1) & 0xFF; return self.rd(0x0100 + self.sp)
    def branch(self, take):
        off = self.imm()
        if take:
            self.pc = (self.pc + (off - 256 if off > 127 else off)) & 0xFFFF
    def cmp_(self, reg, v):
        d = (reg - v) & 0x1FF
        self.c = int(reg >= v); self.nz(d & 0xFF)

    # --- one instruction --------------------------------------------------
    def step(self):
        op = self.imm()
        if   op == 0xA9: self.a = self.nz(self.imm())                       # LDA #
        elif op == 0xA5: self.a = self.nz(self.rd(self.imm()))              # LDA zp
        elif op == 0xAD: self.a = self.nz(self.rd(self.abs_()))             # LDA abs
        elif op == 0xBD: self.a = self.nz(self.rd(self.abs_() + self.x))    # LDA abs,x
        elif op == 0xB9: self.a = self.nz(self.rd(self.abs_() + self.y))    # LDA abs,y
        elif op == 0xA2: self.x = self.nz(self.imm())                       # LDX #
        elif op == 0xAE: self.x = self.nz(self.rd(self.abs_()))             # LDX abs
        elif op == 0xA0: self.y = self.nz(self.imm())                       # LDY #
        elif op == 0xAC: self.y = self.nz(self.rd(self.abs_()))             # LDY abs
        elif op == 0x85: self.wr(self.imm(), self.a)                        # STA zp
        elif op == 0x8D: self.wr(self.abs_(), self.a)                       # STA abs
        elif op == 0x8E: self.wr(self.abs_(), self.x)                       # STX abs
        elif op == 0x8C: self.wr(self.abs_(), self.y)                       # STY abs
        elif op == 0x91:                                                    # STA (zp),y
            zp = self.imm(); p = self.rd(zp) | (self.rd((zp + 1) & 0xFF) << 8)
            self.wr(p + self.y, self.a)
        elif op == 0x29: self.a = self.nz(self.a & self.imm())              # AND #
        elif op == 0x2D: self.a = self.nz(self.a & self.rd(self.abs_()))    # AND abs
        elif op == 0x69:                                                    # ADC #
            v = self.imm(); s = self.a + v + self.c
            self.c = int(s > 0xFF); self.a = self.nz(s)
        elif op == 0x4A:                                                    # LSR A
            self.c = self.a & 1; self.a = self.nz(self.a >> 1)
        elif op == 0x18: self.c = 0                                         # CLC
        elif op == 0x48: self.push(self.a)                                  # PHA
        elif op == 0x68: self.a = self.nz(self.pop())                       # PLA
        elif op == 0xE8: self.x = self.nz(self.x + 1)                       # INX
        elif op == 0x88: self.y = self.nz(self.y - 1)                       # DEY
        elif op == 0xE6:                                                    # INC zp
            a = self.imm(); self.wr(a, self.nz(self.rd(a) + 1))
        elif op == 0xEE:                                                    # INC abs
            a = self.abs_(); self.wr(a, self.nz(self.rd(a) + 1))
        elif op == 0xCE:                                                    # DEC abs
            a = self.abs_(); self.wr(a, self.nz(self.rd(a) - 1))
        elif op == 0xC9: self.cmp_(self.a, self.imm())                      # CMP #
        elif op == 0xCD: self.cmp_(self.a, self.rd(self.abs_()))            # CMP abs
        elif op == 0xE0: self.cmp_(self.x, self.imm())                      # CPX #
        elif op == 0xC0: self.cmp_(self.y, self.imm())                      # CPY #
        elif op == 0x10: self.branch(not self.n)                            # BPL
        elif op == 0x30: self.branch(self.n)                                # BMI
        elif op == 0x90: self.branch(not self.c)                            # BCC
        elif op == 0xB0: self.branch(self.c)                                # BCS
        elif op == 0xD0: self.branch(not self.z)                            # BNE
        elif op == 0xF0: self.branch(self.z)                                # BEQ
        elif op == 0x4C: self.pc = self.abs_()                              # JMP
        elif op == 0x20:                                                    # JSR
            t = self.abs_(); r = (self.pc - 1) & 0xFFFF
            self.push(r >> 8); self.push(r & 0xFF); self.pc = t
        elif op == 0x60:                                                    # RTS
            lo = self.pop(); hi = self.pop(); self.pc = ((hi << 8) | lo) + 1
        elif op == 0x09: self.a = self.nz(self.a | self.imm())               # ORA #
        elif op == 0x0D: self.a = self.nz(self.a | self.rd(self.abs_()))     # ORA abs
        elif op == 0x05: self.a = self.nz(self.a | self.rd(self.imm()))      # ORA zp
        elif op == 0x49: self.a = self.nz(self.a ^ self.imm())               # EOR #
        elif op == 0x6D:                                                     # ADC abs
            v = self.rd(self.abs_()); t = self.a + v + self.c
            self.c = int(t > 0xFF); self.a = self.nz(t)
        elif op == 0xE9:                                                     # SBC #
            v = self.imm(); t = self.a - v - (1 - self.c)
            self.c = int(t >= 0); self.a = self.nz(t)
        elif op == 0x0A:                                                     # ASL A
            self.c = (self.a >> 7) & 1; self.a = self.nz(self.a << 1)
        elif op == 0x86: self.wr(self.imm(), self.x)                         # STX zp
        elif op == 0x84: self.wr(self.imm(), self.y)                         # STY zp
        elif op == 0xA6: self.x = self.nz(self.rd(self.imm()))               # LDX zp
        elif op == 0xA4: self.y = self.nz(self.rd(self.imm()))               # LDY zp
        elif op == 0xAA: self.x = self.nz(self.a)                            # TAX
        elif op == 0xA8: self.y = self.nz(self.a)                            # TAY
        elif op == 0x8A: self.a = self.nz(self.x)                            # TXA
        elif op == 0x98: self.a = self.nz(self.y)                            # TYA
        elif op == 0xCA: self.x = self.nz(self.x - 1)                        # DEX
        elif op == 0xC8: self.y = self.nz(self.y + 1)                        # INY
        elif op == 0x78 or op == 0x58 or op == 0xEA or op == 0xD8 or op == 0xB8:
            pass                                                             # SEI/CLI/NOP/CLD/CLV
        elif op == 0x38: self.c = 1                                          # SEC
        elif op == 0x9D: self.wr(self.abs_() + self.x, self.a)               # STA abs,x
        elif op == 0x99: self.wr(self.abs_() + self.y, self.a)               # STA abs,y
        elif op == 0xB5: self.a = self.nz(self.rd((self.imm() + self.x) & 0xFF))  # LDA zp,x
        elif op == 0x95: self.wr((self.imm() + self.x) & 0xFF, self.a)       # STA zp,x
        else:
            raise RuntimeError(f'neimplementovany opcode ${op:02X} na ${self.pc-1:04X}')


class Machine:
    BASIC_LO, BASIC_HI = 0xA000, 0xBFFF
    PORTB = 0xD301

    def __init__(self, atr, boot, fail=None, basic_on=True, run_inits=True):
        d = open(atr, 'rb').read()
        assert struct.unpack('<H', d[:2])[0] == 0x0296
        self.disk = d[16:]
        self.mem = bytearray(0x10000)
        b = open(boot, 'rb').read()
        self.mem[0x0700:0x0700 + len(b)] = b        # OS boot: 3 sectors -> $0700
        self.cpu = CPU(self.mem)
        self.fail = dict(fail or {})                # sector -> remaining failures
        self.reads = 0
        self.retries = 0
        self.err = None
        self.basic_on = basic_on            # XL/XE default when OPTION is not held
        self.run_inits = run_inits          # False = pretend the xex has no ini segments
        self.blocked = 0                    # writes swallowed by the BASIC ROM
        self.mem[self.PORTB] = 0xFD if basic_on else 0xFF
        cpu = self.cpu
        _wr = cpu.wr
        def wr(a, v):
            a &= 0xFFFF
            if a == self.PORTB:
                self.basic_on = not (v & 0x02)
            elif self.basic_on and self.BASIC_LO <= a <= self.BASIC_HI:
                self.blocked += 1           # BASIC ROM is mapped: the write is lost
                return
            _wr(a, v)
        cpu.wr = wr

    def siov(self):
        c = self.cpu
        sec = c.m[0x030A] | (c.m[0x030B] << 8)
        buf = c.m[0x0304] | (c.m[0x0305] << 8)
        n = c.m[0x0308] | (c.m[0x0309] << 8)
        left = self.fail.get(sec, 0)
        if left:
            self.fail[sec] = left - 1
            self.retries += 1
            c.y = 0x8A                              # device timeout
        else:
            o = (sec - 1) * SECTOR
            self.mem[buf:buf + n] = self.disk[o:o + n]
            self.reads += 1
            c.y = 0x01
        c.m[0x0303] = c.y
        c.n = (c.y >> 7) & 1
        c.z = int(c.y == 0)

    def run(self, max_steps=200_000_000):
        c = self.cpu
        c.pc = 0x0706                               # OS jumps to load_address+6
        c.push(0x99); c.push(0x99)                  # the OS's own return address
        entry_sp = c.sp
        for _ in range(max_steps):
            if c.pc == SIOV:
                self.siov()
                lo = c.pop(); hi = c.pop(); c.pc = ((hi << 8) | lo) + 1
                continue
            if not (LOADER_LO <= c.pc <= LOADER_HI):
                if c.sp < entry_sp:                 # inside an INIT segment
                    if not self.run_inits:          # simulate an xex without them
                        lo = c.pop(); hi = c.pop(); c.pc = ((hi << 8) | lo) + 1
                        continue
                    c.step()                        # ...otherwise really run it
                    continue
                return ('RUN', c.pc)                # the loader JMPed to RUNAD
            if 0x0800 <= c.pc <= 0x087F and self.mem[c.pc] == 0x4C \
                    and (c.pc == self.err_pc()):
                pass
            before = c.pc
            c.step()
            if c.pc == before and self.mem[before] == 0x4C:   # jmp * = halt
                return ('HALT', before)
        return ('TIMEOUT', c.pc)

    def err_pc(self):
        return -1

    def screen_text(self):
        p = self.mem[0x58] | (self.mem[0x59] << 8)
        out = []
        for v in self.mem[p:p + 28]:
            out.append(chr(v + 0x20) if v < 0x40 else chr(v))
        return ''.join(out)


def parse_fail(argv):
    f = {}
    if '--fail' in argv:
        for part in argv[argv.index('--fail') + 1].split(','):
            s, n = part.split(':')
            f[int(s, 0)] = int(n)
    return f


def main():
    # the OS puts SAVMSC somewhere in the display list area; any RAM address does
    fail = parse_fail(sys.argv)
    m = Machine(ATR, BOOT, fail)
    m.mem[0x58], m.mem[0x59] = 0x00, 0x9C           # pretend E: screen at $9C00
    how, where = m.run()
    print(f"vysledok        : {how} na ${where:04X}")
    print(f"sektorov         : {m.reads} precitanych, {m.retries} zlyhanych pokusov")
    print(f"cur_sec / buf_pos: {m.mem[0x0809] | (m.mem[0x080A] << 8)} / {m.mem[0x080B]}")
    if how == 'RUN':
        print(f"RUN adresa       : ${where:04X}  (ma byt $2000)")
        for lab, lo, hi in (('kod   ', 0x2000, 0x3CE6), ('pal   ', 0x9000, 0x95FF),
                            ('fmul  ', 0xA000, 0xA9FF), ('font  ', 0xB000, 0xB6FB)):
            blk = bytes(m.mem[lo:hi + 1])
            print(f"  {lab} ${lo:04X}-${hi:04X}: {len(blk)} B, "
                  f"{'same same' if False else 'nenulovych %d' % sum(1 for v in blk if v)}")
    else:
        print(f"obrazovka        : {m.screen_text()!r}")
    return m


if __name__ == '__main__':
    main()
