#!/usr/bin/env python3
"""sim_hs_sio.py - run the REAL assembled high-speed SIO loader (game_diskio.asm's
hs_read_sector / read_sectors, straight out of the built awgame xex) on a cycle
counting 6502 against a MODELLED SIO2SD.

Why this exists: the high-speed path is only ever entered on a device that answers
command $3F.  SIDE3/AVG/SUB do not, and neither does Altirra with accelerated SIO,
so the whole routine is dead code on every machine the port was developed on -- and
live code on exactly one: a real SIO2SD.  That is where "after the intro only
LOADING... and it freezes" comes from, and it cannot be reproduced by running the
game anywhere else.  So model the wire instead.

Modelled: POKEY serial (AUDCTL/AUDF3-4 bit clock, SKCTL modes, SEROUT/SERIN double
buffering, IRQST bits 3/4/5), PIA CB2 = the SIO COMMAND line, ANTIC VCOUNT, the OS
jiffy clock RTCLOK3, and a drive that (like real hardware) only hears a command
frame if COMMAND was asserted long enough BEFORE the first bit.

    python tools/sim_hs_sio.py            # all checks
    python tools/sim_hs_sio.py -v         # + per-frame trace
"""
import os, sys, struct

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
XEX = os.path.join(PROJ, 'out', 'awgame_test.xex')
LAB = os.path.join(PROJ, 'out', 'awgame_test.lab')

CYC_PER_LINE = 114
LINES_PER_FRAME = 312                       # PAL
CYC_PER_FRAME = CYC_PER_LINE * LINES_PER_FRAME
MHZ = 1.7734                                # PAL colour clock / 2, cycles per us

DAUX1, DAUX2 = 0x030A, 0x030B
DBUFLO, DBUFHI = 0x0304, 0x0305
RTCLOK3 = 0x0014


# ---------------------------------------------------------------------------
# 6502
# ---------------------------------------------------------------------------
CYC = [7,6,0,8,3,3,5,5,3,2,2,2,4,4,6,6, 2,5,0,8,4,4,6,6,2,4,2,7,4,4,7,7,
       6,6,0,8,3,3,5,5,4,2,2,2,4,4,6,6, 2,5,0,8,4,4,6,6,2,4,2,7,4,4,7,7,
       6,6,0,8,3,3,5,5,3,2,2,2,3,4,6,6, 2,5,0,8,4,4,6,6,2,4,2,7,4,4,7,7,
       6,6,0,8,3,3,5,5,4,2,2,2,5,4,6,6, 2,5,0,8,4,4,6,6,2,4,2,7,4,4,7,7,
       2,6,2,6,3,3,3,3,2,2,2,2,4,4,4,4, 2,6,0,6,4,4,4,4,2,5,2,5,5,5,5,5,
       2,6,2,6,3,3,3,3,2,2,2,2,4,4,4,4, 2,5,0,5,4,4,4,4,2,4,2,4,4,4,4,4,
       2,6,2,8,3,3,5,5,2,2,2,2,4,4,6,6, 2,5,0,8,4,4,6,6,2,4,2,7,4,4,7,7,
       2,6,2,8,3,3,5,5,2,2,2,2,4,4,6,6, 2,5,0,8,4,4,6,6,2,4,2,7,4,4,7,7]


class CPU:
    def __init__(self, m):
        self.m = m                          # Machine (rd/wr hooks)
        self.a = self.x = self.y = 0
        self.sp = 0xFD
        self.pc = 0
        self.c = self.z = self.i = self.d = self.v = self.n = 0

    # --- flag helpers ---
    def p(self):
        return (self.n << 7) | (self.v << 6) | 0x20 | (self.d << 3) | \
               (self.i << 2) | (self.z << 1) | self.c

    def setp(self, v):
        self.n = (v >> 7) & 1; self.v = (v >> 6) & 1; self.d = (v >> 3) & 1
        self.i = (v >> 2) & 1; self.z = (v >> 1) & 1; self.c = v & 1

    def nz(self, v):
        v &= 0xFF; self.z = int(v == 0); self.n = v >> 7
        return v

    def push(self, v):
        self.m.wr(0x100 + self.sp, v); self.sp = (self.sp - 1) & 0xFF

    def pop(self):
        self.sp = (self.sp + 1) & 0xFF; return self.m.rd(0x100 + self.sp)

    def fetch(self):
        v = self.m.rd(self.pc); self.pc = (self.pc + 1) & 0xFFFF; return v

    def fetchw(self):
        return self.fetch() | (self.fetch() << 8)

    # --- addressing ---
    def addr(self, mode):
        m = self.m
        if mode == 'zp':   return self.fetch()
        if mode == 'zpx':  return (self.fetch() + self.x) & 0xFF
        if mode == 'zpy':  return (self.fetch() + self.y) & 0xFF
        if mode == 'abs':  return self.fetchw()
        if mode == 'abx':
            b = self.fetchw(); a = (b + self.x) & 0xFFFF
            if (b ^ a) >> 8: self.extra += 1
            return a
        if mode == 'aby':
            b = self.fetchw(); a = (b + self.y) & 0xFFFF
            if (b ^ a) >> 8: self.extra += 1
            return a
        if mode == 'inx':
            z = (self.fetch() + self.x) & 0xFF
            return m.rd(z) | (m.rd((z + 1) & 0xFF) << 8)
        if mode == 'iny':
            z = self.fetch()
            b = m.rd(z) | (m.rd((z + 1) & 0xFF) << 8); a = (b + self.y) & 0xFFFF
            if (b ^ a) >> 8: self.extra += 1
            return a
        raise AssertionError(mode)

    def step(self):
        op = self.fetch()
        self.extra = 0
        cyc = CYC[op]
        if cyc == 0:
            raise RuntimeError(f'neimplementovany/illegal opcode ${op:02X} na ${self.pc-1:04X}')
        h = OPS.get(op)
        if h is None:
            raise RuntimeError(f'neimplementovany opcode ${op:02X} na ${self.pc-1:04X}')
        h(self)
        return cyc + self.extra


def _rd(mode):
    def f(c):
        if mode == 'imm': return c.fetch()
        return c.m.rd(c.addr(mode))
    return f


def build_ops():
    o = {}

    def alu(name, modes, fn):
        for op, mode in modes:
            def h(c, mode=mode, fn=fn):
                v = c.fetch() if mode == 'imm' else c.m.rd(c.addr(mode))
                fn(c, v)
            o[op] = h

    def do_ora(c, v): c.a = c.nz(c.a | v)
    def do_and(c, v): c.a = c.nz(c.a & v)
    def do_eor(c, v): c.a = c.nz(c.a ^ v)

    def do_adc(c, v):
        s = c.a + v + c.c
        c.v = int((~(c.a ^ v) & (c.a ^ s) & 0x80) != 0)
        c.c = int(s > 0xFF); c.a = c.nz(s)

    def do_sbc(c, v):
        s = c.a - v - (1 - c.c)
        c.v = int(((c.a ^ v) & (c.a ^ s) & 0x80) != 0)
        c.c = int(s >= 0); c.a = c.nz(s)

    def do_lda(c, v): c.a = c.nz(v)
    def do_ldx(c, v): c.x = c.nz(v)
    def do_ldy(c, v): c.y = c.nz(v)

    def cmpr(get):
        def f(c, v):
            r = get(c); d = (r - v) & 0x1FF
            c.c = int(r >= v); c.nz(d & 0xFF)
        return f

    def do_bit(c, v):
        c.z = int((c.a & v) == 0); c.n = (v >> 7) & 1; c.v = (v >> 6) & 1

    M_ALU = lambda b: [(b+0x09,'imm'),(b+0x05,'zp'),(b+0x15,'zpx'),(b+0x0D,'abs'),
                       (b+0x1D,'abx'),(b+0x19,'aby'),(b+0x01,'inx'),(b+0x11,'iny')]
    alu('ORA', M_ALU(0x00), do_ora)
    alu('AND', M_ALU(0x20), do_and)
    alu('EOR', M_ALU(0x40), do_eor)
    alu('ADC', M_ALU(0x60), do_adc)
    alu('LDA', M_ALU(0xA0), do_lda)
    alu('CMP', M_ALU(0xC0), cmpr(lambda c: c.a))
    alu('SBC', M_ALU(0xE0), do_sbc)
    alu('LDX', [(0xA2,'imm'),(0xA6,'zp'),(0xB6,'zpy'),(0xAE,'abs'),(0xBE,'aby')], do_ldx)
    alu('LDY', [(0xA0,'imm'),(0xA4,'zp'),(0xB4,'zpx'),(0xAC,'abs'),(0xBC,'abx')], do_ldy)
    alu('CPX', [(0xE0,'imm'),(0xE4,'zp'),(0xEC,'abs')], cmpr(lambda c: c.x))
    alu('CPY', [(0xC0,'imm'),(0xC4,'zp'),(0xCC,'abs')], cmpr(lambda c: c.y))
    alu('BIT', [(0x24,'zp'),(0x2C,'abs')], do_bit)

    def store(op, mode, reg):
        def h(c, mode=mode, reg=reg):
            c.m.wr(c.addr(mode), getattr(c, reg))
        o[op] = h
    for op, mode in [(0x85,'zp'),(0x95,'zpx'),(0x8D,'abs'),(0x9D,'abx'),
                     (0x99,'aby'),(0x81,'inx'),(0x91,'iny')]:
        store(op, mode, 'a')
    for op, mode in [(0x86,'zp'),(0x96,'zpy'),(0x8E,'abs')]:
        store(op, mode, 'x')
    for op, mode in [(0x84,'zp'),(0x94,'zpx'),(0x8C,'abs')]:
        store(op, mode, 'y')

    def rmw(op, mode, fn):
        def h(c, mode=mode, fn=fn):
            a = c.addr(mode); c.m.wr(a, fn(c, c.m.rd(a)))
        o[op] = h

    def f_asl(c, v):
        c.c = v >> 7; return c.nz(v << 1)

    def f_lsr(c, v):
        c.c = v & 1; return c.nz(v >> 1)

    def f_rol(c, v):
        r = (v << 1) | c.c; c.c = v >> 7; return c.nz(r)

    def f_ror(c, v):
        r = (v >> 1) | (c.c << 7); c.c = v & 1; return c.nz(r)

    def f_inc(c, v): return c.nz(v + 1)
    def f_dec(c, v): return c.nz(v - 1)

    for base, fn in ((0x06, f_asl), (0x46, f_lsr), (0x26, f_rol), (0x66, f_ror)):
        for off, mode in ((0x00,'zp'),(0x10,'zpx'),(0x08,'abs'),(0x18,'abx')):
            rmw(base + off, mode, fn)
    for op, mode in ((0xE6,'zp'),(0xF6,'zpx'),(0xEE,'abs'),(0xFE,'abx')):
        rmw(op, mode, f_inc)
    for op, mode in ((0xC6,'zp'),(0xD6,'zpx'),(0xCE,'abs'),(0xDE,'abx')):
        rmw(op, mode, f_dec)

    o[0x0A] = lambda c: setattr(c, 'a', f_asl(c, c.a))
    o[0x4A] = lambda c: setattr(c, 'a', f_lsr(c, c.a))
    o[0x2A] = lambda c: setattr(c, 'a', f_rol(c, c.a))
    o[0x6A] = lambda c: setattr(c, 'a', f_ror(c, c.a))

    def branch(op, cond):
        def h(c, cond=cond):
            d = c.fetch()
            if cond(c):
                t = (c.pc + (d - 256 if d > 127 else d)) & 0xFFFF
                c.extra += 2 if (t ^ c.pc) >> 8 else 1
                c.pc = t
        o[op] = h
    branch(0x10, lambda c: not c.n); branch(0x30, lambda c: c.n)
    branch(0x50, lambda c: not c.v); branch(0x70, lambda c: c.v)
    branch(0x90, lambda c: not c.c); branch(0xB0, lambda c: c.c)
    branch(0xD0, lambda c: not c.z); branch(0xF0, lambda c: c.z)

    o[0x4C] = lambda c: setattr(c, 'pc', c.fetchw())

    def jmpind(c):
        a = c.fetchw()
        lo = c.m.rd(a); hi = c.m.rd((a & 0xFF00) | ((a + 1) & 0xFF))
        c.pc = lo | (hi << 8)
    o[0x6C] = jmpind

    def jsr(c):
        t = c.fetchw(); r = (c.pc - 1) & 0xFFFF
        c.push(r >> 8); c.push(r & 0xFF); c.pc = t
    o[0x20] = jsr

    def rts(c):
        lo = c.pop(); hi = c.pop(); c.pc = ((hi << 8) | lo + 0) + 1 & 0xFFFF
    o[0x60] = rts

    def rti(c):
        c.setp(c.pop()); lo = c.pop(); hi = c.pop(); c.pc = lo | (hi << 8)
    o[0x40] = rti

    o[0x48] = lambda c: c.push(c.a)
    o[0x68] = lambda c: setattr(c, 'a', c.nz(c.pop()))
    o[0x08] = lambda c: c.push(c.p() | 0x10)
    o[0x28] = lambda c: c.setp(c.pop())

    def reg(op, fn): o[op] = fn
    reg(0xAA, lambda c: setattr(c, 'x', c.nz(c.a)))
    reg(0xA8, lambda c: setattr(c, 'y', c.nz(c.a)))
    reg(0x8A, lambda c: setattr(c, 'a', c.nz(c.x)))
    reg(0x98, lambda c: setattr(c, 'a', c.nz(c.y)))
    reg(0xBA, lambda c: setattr(c, 'x', c.nz(c.sp)))
    reg(0x9A, lambda c: setattr(c, 'sp', c.x))
    reg(0xE8, lambda c: setattr(c, 'x', c.nz(c.x + 1)))
    reg(0xCA, lambda c: setattr(c, 'x', c.nz(c.x - 1)))
    reg(0xC8, lambda c: setattr(c, 'y', c.nz(c.y + 1)))
    reg(0x88, lambda c: setattr(c, 'y', c.nz(c.y - 1)))
    reg(0x18, lambda c: setattr(c, 'c', 0)); reg(0x38, lambda c: setattr(c, 'c', 1))
    reg(0x58, lambda c: setattr(c, 'i', 0)); reg(0x78, lambda c: setattr(c, 'i', 1))
    reg(0xB8, lambda c: setattr(c, 'v', 0))
    reg(0xD8, lambda c: setattr(c, 'd', 0)); reg(0xF8, lambda c: setattr(c, 'd', 1))
    reg(0xEA, lambda c: None)
    return o


OPS = build_ops()


# ---------------------------------------------------------------------------
# the drive on the other end of the cable
# ---------------------------------------------------------------------------
class Drive:
    """A SIO2SD-ish ultra-speed device.

    cmd_setup_us : how long COMMAND must be asserted before the FIRST bit of the
                   frame for the device to hear it at all.  Real adapters need this
                   (the Atari OS gives every device ~725 us -- see `SID` in the OS
                   ROM); a firmware has to catch the COMMAND edge and re-arm its
                   UART at the turbo rate inside that window.
    hs_index     : answer to command $3F.  None = the device ignores $3F.
    """

    def __init__(self, disk, cmd_setup_us=400.0, hs_index=0x0A,
                 ack_us=300.0, complete_us=1500.0, answer=True, corrupt=False,
                 fail_first=0):
        self.disk = disk
        self.cmd_setup = cmd_setup_us * MHZ
        self.hs_index = hs_index
        self.ack_us, self.complete_us = ack_us, complete_us
        self.answer = answer
        self.corrupt = corrupt
        self.fail_first = fail_first
        self.reset()

    def reset(self):
        self.cmd_at = None                  # cycle COMMAND went low
        self.frame = []                     # bytes heard while COMMAND was low
        self.first_bit_at = None
        self.deaf = False                   # frame started too early -> missed
        self.log = []

    # --- wire events -------------------------------------------------------
    def command(self, asserted, cyc):
        if asserted:
            self.cmd_at = cyc; self.frame = []; self.first_bit_at = None
            self.deaf = False
        else:
            self.finish_frame(cyc)

    def byte_from_atari(self, val, start_cyc, end_cyc, cpb):
        if self.cmd_at is None:
            return                          # data frame from the computer: not used
        if self.first_bit_at is None:
            self.first_bit_at = start_cyc
            setup = start_cyc - self.cmd_at
            if setup < self.cmd_setup:
                self.deaf = True
                self.log.append(f'COMMAND drzany len {setup/MHZ:.0f} us pred prvym '
                                f'bitom (potrebuje {self.cmd_setup/MHZ:.0f}) -> ramec nepocut')
            self.cpb = cpb
        self.frame.append(val)

    # --- response ----------------------------------------------------------
    def finish_frame(self, cyc):
        f, self.frame, self.cmd_at = self.frame, [], None
        if self.deaf or len(f) != 5:
            if not self.deaf and f:
                self.log.append(f'ramec ma {len(f)} bajtov, cakal 5 -> ignorujem')
            return
        chk = 0
        for b in f[:4]:
            chk += b
            chk = (chk & 0xFF) + (chk >> 8)
        if chk != f[4]:
            self.log.append('zly checksum prikazoveho ramca -> NAK')
            return
        dev, cmd, a1, a2 = f[:4]
        if dev != 0x31 or not self.answer:
            return
        sec = a1 | (a2 << 8)
        self.log.append(f'prikaz ${cmd:02X} sektor {sec}')
        if cmd == 0x3F:
            if self.hs_index is None:
                self.log.append('$3F neimplementovane -> ticho (OS bude retryovat)')
                return
            self.queue(cyc, [0x41], [0x43], [self.hs_index], chksum=True)
        elif cmd == 0x52:
            if self.fail_first > 0:
                self.fail_first -= 1
                self.log.append('simulovana chyba citania -> ACK + ERROR')
                self.queue(cyc, [0x41], [0x45], None)
                return
            data = list(self.disk(sec))
            self.queue(cyc, [0x41], [0x43], data, chksum=True)
            if self.corrupt:                # flip a byte AFTER the checksum was
                self.out[2] = (self.out[2][0], self.out[2][1] ^ 0xFF)  # computed

    def queue(self, cyc, ack, complete, data, chksum=False):
        self.out = []                       # (cycle_ready, byte)
        t = cyc + self.ack_us * MHZ
        for b in ack:
            self.out.append((t, b)); t += 10 * self.cpb
        t = max(t, cyc + self.complete_us * MHZ)
        for b in complete:
            self.out.append((t, b)); t += 10 * self.cpb
        if data:
            for b in data:
                self.out.append((t, b)); t += 10 * self.cpb
            if chksum:
                s = 0
                for b in data:
                    s += b
                    s = (s & 0xFF) + (s >> 8)
                self.out.append((t, s)); t += 10 * self.cpb


# ---------------------------------------------------------------------------
# machine: RAM + POKEY + PIA + ANTIC
# ---------------------------------------------------------------------------
class Machine:
    def __init__(self, mem, drive, stall_tx=False):
        self.m = bytearray(mem)
        self.drive = drive
        self.stall_tx = stall_tx
        self.cyc = 0
        self.cpu = CPU(self)
        # POKEY
        self.audctl = 0; self.audf3 = 0; self.audf4 = 0
        self.skctl = 0; self.irqen = 0
        self.tx_hold = None                 # byte waiting in the holding register
        self.tx_end = 0                     # cycle the shift register goes empty
        self.tx_busy = False
        self.rx = []                        # pending (cycle, byte) from the drive
        self.rx_byte = None
        self.rx_overrun = 0
        self.cmd_line = False
        self.bytes_out = 0

    # --- POKEY helpers -----------------------------------------------------
    def cpb(self):                          # cycles per serial bit
        f = self.audf3 | (self.audf4 << 8)
        return 2 * (f + 7)

    def pump(self):
        d = self.drive
        if self.tx_busy and self.cyc >= self.tx_end:
            self.tx_busy = False
            if self.tx_hold is not None:
                b, self.tx_hold = self.tx_hold, None
                self.start_shift(b)
        if getattr(d, 'out', None):
            while d.out and d.out[0][0] <= self.cyc:
                t, b = d.out.pop(0)
                if self.rx_byte is not None:
                    self.rx_overrun += 1
                self.rx_byte = b

    def start_shift(self, b):
        cpb = self.cpb()
        st = self.cyc
        self.tx_busy = True
        self.tx_end = st + 10 * cpb
        self.bytes_out += 1
        self.drive.byte_from_atari(b, st, self.tx_end, cpb)

    # --- bus ---------------------------------------------------------------
    def rd(self, a):
        a &= 0xFFFF
        self.pump()
        if a == RTCLOK3:
            return (self.cyc // CYC_PER_FRAME) & 0xFF
        if a == 0xD40B:                                 # VCOUNT
            return (self.cyc // (2 * CYC_PER_LINE)) % LINES_PER_FRAME
        if 0xD200 <= a <= 0xD20F:
            r = a & 0x0F
            if r == 0x0D:                               # SERIN
                b, self.rx_byte = self.rx_byte, None
                return 0 if b is None else b
            if r == 0x0E:                               # IRQST (0 = pending)
                st = 0xFF
                if not self.stall_tx:
                    if self.tx_hold is None and (self.skctl & 0x70) == 0x20:
                        st &= ~0x10 & 0xFF              # SEROR: holding empty
                    if (not self.tx_busy) and self.tx_hold is None and self.bytes_out:
                        st &= ~0x08 & 0xFF              # SEROC: everything shifted
                if self.rx_byte is not None:
                    st &= ~0x20 & 0xFF                  # SERIN
                return st | (~self.irqen & 0xFF & 0x38)  # a disabled IRQ never shows
            return 0
        if a == 0xD303:
            return 0x3C
        return self.m[a]

    def wr(self, a, v):
        a &= 0xFFFF; v &= 0xFF
        self.pump()
        if 0xD200 <= a <= 0xD20F:
            r = a & 0x0F
            if r == 0x04: self.audf3 = v
            elif r == 0x06: self.audf4 = v
            elif r == 0x08: self.audctl = v
            elif r == 0x0A: pass                        # SKRES
            elif r == 0x0D:                             # SEROUT
                if self.tx_busy:
                    self.tx_hold = v
                else:
                    self.start_shift(v)
            elif r == 0x0E: self.irqen = v
            elif r == 0x0F:
                self.skctl = v
                if v == 0:
                    self.tx_busy = False; self.tx_hold = None
            return
        if a == 0xD303:                                 # PBCTL: bit3 = COMMAND
            asserted = (v & 0x08) == 0
            if asserted != self.cmd_line:
                self.cmd_line = asserted
                self.drive.command(asserted, self.cyc)
            return
        if a >= 0xD000:
            return
        self.m[a] = v

    # --- OS SIO ($E459), modelled at the Python level ----------------------
    #   success: one 19200 baud transaction.  failure: the OS burns CRETRI+1 = 14
    #   command frames x DRETRI+1 = 2 device passes x CTIM = 128 vblanks before it
    #   gives up -- ~72 s on PAL.  That number is the whole point of sio_settle.
    def siov(self):
        c, mm, d = self.cpu, self.m, self.drive
        dev, cmd = mm[0x0300], mm[0x0302]
        buf = mm[0x0304] | (mm[0x0305] << 8)
        n = mm[0x0308] | (mm[0x0309] << 8)
        sec = mm[0x030A] | (mm[0x030B] << 8)
        ok = d.answer and dev == 0x31
        if cmd == 0x3F and d.hs_index is None:
            ok = False
        if cmd == 0x52 and d.fail_first > 0:
            d.fail_first -= 1; ok = False
        self.siov_calls = getattr(self, 'siov_calls', 0) + 1
        if ok:
            self.cyc += int((725.0 + (5 + 2 + n + 1) * 10 * 52.1 + 1500.0) * MHZ)
            if cmd == 0x52:
                mm[buf:buf + n] = d.disk(sec)[:n]
            elif cmd == 0x3F:
                mm[buf] = d.hs_index
            c.y = 0x01
        else:
            self.cyc += 28 * 128 * CYC_PER_FRAME
            c.y = 0x8A
        mm[0x0303] = c.y
        c.n = (c.y >> 7) & 1; c.z = int(c.y == 0)

    # --- run ---------------------------------------------------------------
    def call(self, addr, budget=40_000_000):
        c = self.cpu
        c.pc = addr
        c.sp = 0xFD
        c.push(0x99); c.push(0x98)                      # return to $9999
        steps = 0
        while c.pc != 0x9999:
            if c.pc == 0xE459:
                self.siov()
                lo = c.pop(); hi = c.pop(); c.pc = ((hi << 8) | lo) + 1
                continue
            self.cyc += c.step()
            steps += 1
            if self.cyc > budget:
                return False
        return True


# ---------------------------------------------------------------------------
def load_xex(path):
    d = open(path, 'rb').read()
    mem = bytearray(0x10000)
    i = 2 if d[:2] == b'\xff\xff' else 0
    while i + 4 <= len(d):
        s, e = struct.unpack_from('<HH', d, i)
        if s == 0xFFFF:
            i += 2; continue
        i += 4; n = (e - s + 1) & 0xFFFF
        if s not in (0x02E0, 0x02E2):
            mem[s:s + n] = d[i:i + n]
        i += n
    return mem


def labels(path):
    out = {}
    for line in open(path):
        f = line.split()
        if len(f) >= 3 and len(f[1]) == 4:
            try:
                out[f[2].upper()] = int(f[1], 16)
            except ValueError:
                pass
    return out


def sector(n):
    """deterministic pseudo-data, so a wrong byte anywhere is visible"""
    return bytes(((n * 7 + i * 31 + (i >> 3)) & 0xFF) for i in range(128))


# ---------------------------------------------------------------------------
def main():
    verbose = '-v' in sys.argv
    if not os.path.exists(XEX):
        sys.exit(f'chyba {XEX} - najprv:\n'
                 f'  mads.exe src_game/awgame.asm -o:out/awgame_test.xex '
                 f'-t:out/awgame_test.lab -d:GAME_SEC=4')
    mem = load_xex(XEX)
    L = labels(LAB)
    HS = L['HS_READ_SECTOR']; RS = L['READ_SECTORS']; POLL = L['HS_POLL']
    HSDIV = 0xB3D6
    fails = []

    def check(name, ok, detail=''):
        print(f"  {'OK  ' if ok else 'CHYBA'} {name}{'  ' + detail if detail else ''}")
        if not ok:
            fails.append(name)

    def hs_read(drive, sec=1000, dest=0x5000, div=0x0A, budget=40_000_000,
                stall=False, patch_nodelay=False):
        m = Machine(mem, drive, stall_tx=stall)
        if patch_nodelay:                      # simulate the OLD code: no COMMAND hold
            a = HS
            while a < HS + 200:
                if m.m[a] == 0x20 and (m.m[a+1] | (m.m[a+2] << 8)) == L['SIO_CMD_DELAY']:
                    m.m[a] = m.m[a+1] = m.m[a+2] = 0xEA
                    break
                a += 1
        m.m[HSDIV] = div
        m.m[DAUX1] = sec & 0xFF; m.m[DAUX2] = sec >> 8
        m.m[DBUFLO] = dest & 0xFF; m.m[DBUFHI] = dest >> 8
        done = m.call(HS, budget)
        return m, done

    print('=== 1. zdravy SIO2SD (hs index $0A = 52 kb/s) ===')
    d = Drive(sector, cmd_setup_us=400)
    m, done = hs_read(d)
    if verbose:
        for l in d.log: print('     dev:', l)
    got = bytes(m.m[0x5000:0x5080])
    check('rutina dobehla', done)
    check('C=0 (uspech)', done and m.cpu.c == 0)
    check('data sedia', got == sector(1000),
          f'{sum(a != b for a, b in zip(got, sector(1000)))} zlych bajtov')
    check('ziadny overrun prijmu', m.rx_overrun == 0)
    us = m.cyc / MHZ
    print(f'     -> {m.cyc} cyklov = {us/1000:.2f} ms na sektor '
          f'({1000/(us/1000):.0f} sektorov/s)')

    print('=== 2. REGRESIA: to iste bez drzania COMMAND (stara verzia) ===')
    d = Drive(sector, cmd_setup_us=400)
    m, done = hs_read(d, patch_nodelay=True)
    for l in d.log[:2]: print('     dev:', l)
    check('stara verzia u tohto zariadenia ZLYHA', done and m.cpu.c == 1)
    print(f'     -> {m.cyc/MHZ/1000:.0f} ms premrhanych na kazdom sektore, '
          f'potom fallback na 19200')

    print('=== 3. zariadenie neodpoveda vobec ===')
    d = Drive(sector, answer=False)
    m, done = hs_read(d)
    check('dobehne (netuhne)', done)
    check('C=1 (chyba)', done and m.cpu.c == 1)
    print(f'     -> timeout {m.cyc/MHZ/1000:.0f} ms')

    print('=== 4. zaseknuty POKEY vysielac (test ohranicenia ?wl/?txc) ===')
    d = Drive(sector)
    m, done = hs_read(d, stall=True, budget=60_000_000)
    check('dobehne namiesto vecnej slucky', done,
          f'{m.cyc/MHZ/1000:.0f} ms, C={m.cpu.c}  (pred opravou: nekonecna slucka)')

    print('=== 5. poskodeny datovy ramec (checksum) ===')
    d = Drive(sector, corrupt=True)
    m, done = hs_read(d)
    check('dobehne', done)
    check('C=1 - checksum chytil chybu', done and m.cpu.c == 1)

    print('=== 6. read_sectors: 3 chybne pokusy, stvrty prejde (RS_RETRY=4) ===')
    d = Drive(sector, fail_first=3)
    m = Machine(mem, d)
    m.m[HSDIV] = 0x0A
    m.m[DAUX1] = 200; m.m[DAUX2] = 0
    m.m[DBUFLO] = 0x00; m.m[DBUFHI] = 0x60
    m.cpu.x = 1
    done = m.call(RS, 400_000_000)
    check('dobehne', done)
    check('C=0 po retry', done and m.cpu.c == 0)
    check('sektor nacitany spravne', bytes(m.m[0x6000:0x6080]) == sector(200))

    print('=== 7. read_sectors: trvalo mrtve zariadenie -> hlasi chybu ===')
    d = Drive(sector, answer=False)
    m = Machine(mem, d)
    m.m[HSDIV] = 0x0A
    m.m[DAUX1] = 5; m.m[DAUX2] = 0
    m.m[DBUFLO] = 0x00; m.m[DBUFHI] = 0x60
    m.cpu.x = 1
    done = m.call(RS, 1_200_000_000)
    check('dobehne', done)
    check('C=1 - chyba sa hlasi hore', done and m.cpu.c == 1)

    print('=== 8. rychlostna tabulka (part 16002 = 1511 sektorov) ===')
    for div, lbl in ((0x28, '19200 (stock)'), (0x10, '38 kb/s'),
                     (0x0A, '52 kb/s'), (0x06, '68 kb/s')):
        d = Drive(sector, cmd_setup_us=400)
        m, done = hs_read(d, div=div)
        if done and m.cpu.c == 0:
            print(f'     div ${div:02X} {lbl:14}: {m.cyc/MHZ/1000:6.2f} ms/sektor '
                  f'-> part 16002 za {1511*m.cyc/MHZ/1e6:5.1f} s')

    print()
    if fails:
        print(f'ZLYHALO: {len(fails)} kontrol -> {fails}')
        return 1
    print('vsetky kontroly presli')
    return 0


if __name__ == '__main__':
    sys.exit(main())
