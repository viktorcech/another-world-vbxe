#!/usr/bin/env python3
"""_sim6502.py - a FAST cycle-counting NMOS 6502 for whole-frame benches.

The interpreter loop is GENERATED Python (one function, registers in locals, a
binary if-tree on the opcode, every opcode inlined), so a frame of game work runs in
a fraction of a second instead of the several seconds tools/_cpu6502.py needs. The
semantics and cycle counts are the NMOS ones _cpu6502.py uses (page-cross +1 on
indexed READS, +1/+2 on taken branches, RMW abs,x = 7); tools/_bench_frame.py
--selftest runs both cores side by side on the real game code and compares.

Memory model: `m` is 64 KB of RAM. Pages whose `rp[page]` / `wp[page]` byte is 0 go
through the machine's rd(addr) / wr(addr, v) (I/O, MEMAC windows, ROM); zero page and
the stack are always plain RAM. Code is fetched from `m` directly.

Hooks the machine provides:
  trap(pc)          opcode $02 (a KIL on NMOS) = a host trap; the ROM entry points
                    (SIOV) are filled with it. Returns nothing; may change the state.
  spin(pc)          taken backward branch to a target in `spins` (a pure-read spin
                    loop): returns the cycles to fast-forward (whole iterations), or 0.
Profile: pcyc[pc] += cycles of every executed instruction, pcnt[pc] += 1 (when prof is on).
"""


class Halt(Exception):
    pass


# (mnemonic, mode, base cycles) for every documented opcode + $E2 (NOP #imm)
def _table():
    t = {}
    alu = [('ora', 0x00), ('and', 0x20), ('eor', 0x40), ('adc', 0x60), ('sta', 0x80),
           ('lda', 0xA0), ('cmp', 0xC0), ('sbc', 0xE0)]
    for mn, b in alu:
        for off, mode in ((0x01, 'izx'), (0x05, 'zp'), (0x09, 'imm'), (0x0D, 'abs'),
                          (0x11, 'izy'), (0x15, 'zpx'), (0x19, 'aby'), (0x1D, 'abx')):
            if mn == 'sta' and mode == 'imm':
                continue
            t[b + off] = (mn, mode)
    for mn, b in (('asl', 0x00), ('rol', 0x20), ('lsr', 0x40), ('ror', 0x60)):
        for off, mode in ((0x06, 'zp'), (0x0A, 'acc'), (0x0E, 'abs'), (0x16, 'zpx'), (0x1E, 'abx')):
            t[b + off] = (mn, mode)
    for mn, lst in (('inc', [(0xE6, 'zp'), (0xEE, 'abs'), (0xF6, 'zpx'), (0xFE, 'abx')]),
                    ('dec', [(0xC6, 'zp'), (0xCE, 'abs'), (0xD6, 'zpx'), (0xDE, 'abx')]),
                    ('ldx', [(0xA2, 'imm'), (0xA6, 'zp'), (0xAE, 'abs'), (0xB6, 'zpy'), (0xBE, 'aby')]),
                    ('ldy', [(0xA0, 'imm'), (0xA4, 'zp'), (0xAC, 'abs'), (0xB4, 'zpx'), (0xBC, 'abx')]),
                    ('stx', [(0x86, 'zp'), (0x8E, 'abs'), (0x96, 'zpy')]),
                    ('sty', [(0x84, 'zp'), (0x8C, 'abs'), (0x94, 'zpx')]),
                    ('cpx', [(0xE0, 'imm'), (0xE4, 'zp'), (0xEC, 'abs')]),
                    ('cpy', [(0xC0, 'imm'), (0xC4, 'zp'), (0xCC, 'abs')]),
                    ('bit', [(0x24, 'zp'), (0x2C, 'abs')]),
                    ('jmp', [(0x4C, 'abs'), (0x6C, 'ind')]),
                    ('jsr', [(0x20, 'abs')])):
        for op, mode in lst:
            t[op] = (mn, mode)
    for op, mn in ((0x10, 'bpl'), (0x30, 'bmi'), (0x50, 'bvc'), (0x70, 'bvs'), (0x90, 'bcc'),
                   (0xB0, 'bcs'), (0xD0, 'bne'), (0xF0, 'beq')):
        t[op] = (mn, 'rel')
    for op, mn in ((0x00, 'brk'), (0x08, 'php'), (0x18, 'clc'), (0x28, 'plp'), (0x38, 'sec'),
                   (0x40, 'rti'), (0x48, 'pha'), (0x58, 'cli'), (0x60, 'rts'), (0x68, 'pla'),
                   (0x78, 'sei'), (0x88, 'dey'), (0x8A, 'txa'), (0x98, 'tya'), (0x9A, 'txs'),
                   (0xA8, 'tay'), (0xAA, 'tax'), (0xB8, 'clv'), (0xBA, 'tsx'), (0xC8, 'iny'),
                   (0xCA, 'dex'), (0xD8, 'cld'), (0xE8, 'inx'), (0xEA, 'nop'), (0xF8, 'sed')):
        t[op] = (mn, 'imp')
    t[0xE2] = ('nopi', 'imm')          # undocumented NOP #imm (detect_cpu's SEP #$01 probe)
    t[0x02] = ('trap', 'imp')          # host trap
    return t


OPT = _table()

_READ_MODE_CYC = {'imm': 2, 'zp': 3, 'zpx': 4, 'zpy': 4, 'abs': 4, 'abx': 4, 'aby': 4, 'izx': 6, 'izy': 5}
_STORE_CYC = {'zp': 3, 'zpx': 4, 'zpy': 4, 'abs': 4, 'abx': 5, 'aby': 5, 'izx': 6, 'izy': 6}
_RMW_CYC = {'acc': 2, 'zp': 5, 'zpx': 6, 'abs': 6, 'abx': 7}


def _ea(mode, rd):
    """code lines computing `ea` (and `px` = page-cross flag for reads)."""
    if mode == 'zp':
        return ['ea = m[PC]', 'PC += 1']
    if mode == 'zpx':
        return ['ea = (m[PC] + X) & 255', 'PC += 1']
    if mode == 'zpy':
        return ['ea = (m[PC] + Y) & 255', 'PC += 1']
    if mode == 'abs':
        return ['ea = m[PC] | (m[PC + 1] << 8)', 'PC += 2']
    if mode in ('abx', 'aby'):
        r = 'X' if mode == 'abx' else 'Y'
        L = ['b = m[PC] | (m[PC + 1] << 8)', 'PC += 2', 'ea = (b + %s) & 65535' % r]
        if rd:
            L.append('if (b ^ ea) & 65280: cyc += 1')
        return L
    if mode == 'izx':
        return ['z = (m[PC] + X) & 255', 'PC += 1', 'ea = m[z] | (m[(z + 1) & 255] << 8)']
    if mode == 'izy':
        L = ['z = m[PC]', 'PC += 1', 'b = m[z] | (m[(z + 1) & 255] << 8)', 'ea = (b + Y) & 65535']
        if rd:
            L.append('if (b ^ ea) & 65280: cyc += 1')
        return L
    raise ValueError(mode)


def _load(mode):
    """lines leaving the operand value in v."""
    if mode == 'imm':
        return ['v = m[PC]', 'PC += 1']
    L = _ea(mode, True)
    if mode in ('zp', 'zpx', 'zpy'):
        L.append('v = m[ea]')
    else:
        L.append('v = m[ea] if rp[ea >> 8] else RD(ea)')
    return L


def _store(mode, val):
    L = _ea(mode, False)
    if mode in ('zp', 'zpx', 'zpy'):
        L.append('m[ea] = %s' % val)
    else:
        L.append('if wp[ea >> 8]: m[ea] = %s' % val)
        L.append('else: WR(ea, %s)' % val)
    return L


def _op_code(op):
    mn, mode = OPT[op]
    L = []
    if mn in ('lda', 'ldx', 'ldy'):
        L += _load(mode)
        r = mn[2].upper()
        L += ['%s = v' % r, 'zv = nv = v', 'cyc += %d' % _READ_MODE_CYC[mode]]
    elif mn in ('ora', 'and', 'eor'):
        L += _load(mode)
        o = {'ora': '|', 'and': '&', 'eor': '^'}[mn]
        L += ['A = A %s v' % o, 'zv = nv = A', 'cyc += %d' % _READ_MODE_CYC[mode]]
    elif mn == 'adc':
        L += _load(mode)
        L += ['if D: raise Halt("decimal adc at $%04X" % (PC - 2))',
              's = A + v + C', 'r = s & 255', 'V = 1 if (~(A ^ v) & (A ^ r)) & 128 else 0',
              'C = 1 if s > 255 else 0', 'A = zv = nv = r', 'cyc += %d' % _READ_MODE_CYC[mode]]
    elif mn == 'sbc':
        L += _load(mode)
        L += ['if D: raise Halt("decimal sbc at $%04X" % (PC - 2))',
              'v ^= 255', 's = A + v + C', 'r = s & 255', 'V = 1 if (~(A ^ v) & (A ^ r)) & 128 else 0',
              'C = 1 if s > 255 else 0', 'A = zv = nv = r', 'cyc += %d' % _READ_MODE_CYC[mode]]
    elif mn in ('cmp', 'cpx', 'cpy'):
        L += _load(mode)
        r = {'cmp': 'A', 'cpx': 'X', 'cpy': 'Y'}[mn]
        L += ['C = 1 if %s >= v else 0' % r, 'zv = nv = (%s - v) & 255' % r,
              'cyc += %d' % _READ_MODE_CYC[mode]]
    elif mn == 'bit':
        L += _load(mode)
        L += ['zv = A & v', 'nv = v', 'V = (v >> 6) & 1', 'cyc += %d' % _READ_MODE_CYC[mode]]
    elif mn in ('sta', 'stx', 'sty'):
        L += _store(mode, mn[2].upper())
        L.append('cyc += %d' % _STORE_CYC[mode])
    elif mn in ('asl', 'lsr', 'rol', 'ror', 'inc', 'dec'):
        f = {'asl': ['C = v >> 7', 'v = (v << 1) & 255'],
             'lsr': ['C = v & 1', 'v >>= 1'],
             'rol': ['c0 = C', 'C = v >> 7', 'v = ((v << 1) & 255) | c0'],
             'ror': ['c0 = C', 'C = v & 1', 'v = (v >> 1) | (c0 << 7)'],
             'inc': ['v = (v + 1) & 255'],
             'dec': ['v = (v - 1) & 255']}[mn]
        if mode == 'acc':
            L += ['v = A'] + f + ['A = zv = nv = v']
        else:
            L += _ea(mode, False)
            if mode in ('zp', 'zpx'):
                L += ['v = m[ea]'] + f + ['m[ea] = zv = nv = v']
            else:
                L += ['v = m[ea] if rp[ea >> 8] else RD(ea)'] + f + ['zv = nv = v',
                      'if wp[ea >> 8]: m[ea] = v', 'else: WR(ea, v)']
        L.append('cyc += %d' % _RMW_CYC[mode])
    elif mode == 'rel':
        cond = {'bpl': 'not (nv & 128)', 'bmi': 'nv & 128', 'bvc': 'not V', 'bvs': 'V',
                'bcc': 'not C', 'bcs': 'C', 'bne': 'zv', 'beq': 'not zv'}[mn]
        L += ['o = m[PC]', 'PC += 1', 'cyc += 2',
              'if %s:' % cond,
              '    t = (PC + (o - 256 if o > 127 else o)) & 65535',
              '    cyc += 2 if (t ^ PC) & 65280 else 1',
              '    if t < PC and t in spins:',
              '        k = SPIN(t, cyc)',
              '        if k:',
              '            cyc += k',
              '    PC = t']
    elif mn == 'jmp':
        if mode == 'abs':
            L += ['PC = m[PC] | (m[PC + 1] << 8)', 'cyc += 3']
        else:
            L += ['p = m[PC] | (m[PC + 1] << 8)',
                  'lo = m[p] if rp[p >> 8] else RD(p)',
                  'q = (p & 65280) | ((p + 1) & 255)',
                  'hi = m[q] if rp[q >> 8] else RD(q)',
                  'PC = lo | (hi << 8)', 'cyc += 5']
    elif mn == 'jsr':
        L += ['t = m[PC] | (m[PC + 1] << 8)', 'r = PC + 1',
              'm[256 + S] = r >> 8', 'S = (S - 1) & 255', 'm[256 + S] = r & 255', 'S = (S - 1) & 255',
              'PC = t', 'cyc += 6']
    elif mn == 'rts':
        L += ['S = (S + 1) & 255', 'lo = m[256 + S]', 'S = (S + 1) & 255', 'hi = m[256 + S]',
              'PC = ((hi << 8) | lo) + 1 & 65535', 'cyc += 6']
    elif mn == 'rti':
        L += ['S = (S + 1) & 255', 'p = m[256 + S]',
              'nv = p & 128', 'V = (p >> 6) & 1', 'D = (p >> 3) & 1', 'I = (p >> 2) & 1',
              'zv = 0 if p & 2 else 1', 'C = p & 1',
              'S = (S + 1) & 255', 'lo = m[256 + S]', 'S = (S + 1) & 255', 'hi = m[256 + S]',
              'PC = (hi << 8) | lo', 'cyc += 6']
    elif mn == 'pha':
        L += ['m[256 + S] = A', 'S = (S - 1) & 255', 'cyc += 3']
    elif mn == 'php':
        L += ['m[256 + S] = (nv & 128) | (V << 6) | 48 | (D << 3) | (I << 2) | (0 if zv else 2) | C',
              'S = (S - 1) & 255', 'cyc += 3']
    elif mn == 'pla':
        L += ['S = (S + 1) & 255', 'A = zv = nv = m[256 + S]', 'cyc += 4']
    elif mn == 'plp':
        L += ['S = (S + 1) & 255', 'p = m[256 + S]',
              'nv = p & 128', 'V = (p >> 6) & 1', 'D = (p >> 3) & 1', 'I = (p >> 2) & 1',
              'zv = 0 if p & 2 else 1', 'C = p & 1', 'cyc += 4']
    elif mn in ('tax', 'tay', 'txa', 'tya', 'tsx'):
        src, dst = {'tax': ('A', 'X'), 'tay': ('A', 'Y'), 'txa': ('X', 'A'), 'tya': ('Y', 'A'),
                    'tsx': ('S', 'X')}[mn]
        L += ['%s = zv = nv = %s' % (dst, src), 'cyc += 2']
    elif mn == 'txs':
        L += ['S = X', 'cyc += 2']
    elif mn in ('inx', 'iny', 'dex', 'dey'):
        r = mn[2].upper()
        d = '+ 1' if mn[0] == 'i' else '- 1'
        L += ['%s = zv = nv = (%s %s) & 255' % (r, r, d), 'cyc += 2']
    elif mn in ('clc', 'sec', 'cli', 'sei', 'clv', 'cld', 'sed'):
        L += [{'clc': 'C = 0', 'sec': 'C = 1', 'cli': 'I = 0', 'sei': 'I = 1', 'clv': 'V = 0',
               'cld': 'D = 0', 'sed': 'D = 1'}[mn], 'cyc += 2']
    elif mn == 'nop':
        L += ['cyc += 2']
    elif mn == 'nopi':
        L += ['PC += 1', 'cyc += 2']
    elif mn == 'brk':
        L += ['raise Halt("BRK at $%04X" % (PC - 1))']
    elif mn == 'trap':
        L += ['__TRAP__']
    else:
        raise ValueError(mn)
    return L


_STATE = ['A', 'X', 'Y', 'S', 'PC', 'C', 'zv', 'nv', 'V', 'D', 'I', 'cyc']


def _io_sync(cpu, cyc):
    cpu.cyc = cyc                           # returns None: `_io(..) or RD0(a)` reads on


def _gen():
    ops = sorted(OPT)
    out = []
    w = out.append
    w('def run(cpu, limit):')
    w('    m = cpu.m; rp = cpu.rp; wp = cpu.wp; RD = cpu.rd; WR = cpu.wr')
    w('    spins = cpu.spins; SPIN = cpu.spin; pcyc = cpu.pcyc; pcnt = cpu.pcnt; prof = cpu.prof')
    w('    ' + ', '.join(_STATE) + ' = ' + ', '.join('cpu.' + s.lower() for s in _STATE))
    w('    try:')
    w('      while cyc < limit:')
    w('        pc0 = PC')
    w('        cy0 = cyc')
    w('        op = m[PC]')
    w('        PC += 1')

    def tree(lo, hi, ind):
        cand = [o for o in ops if lo <= o < hi]
        if not cand:
            w(ind + 'raise Halt("illegal opcode $%02X at $%04X" % (op, pc0))')
            return
        if len(cand) == 1:
            o = cand[0]
            w(ind + 'if op != %d: raise Halt("illegal opcode $%%02X at $%%04X" %% (op, pc0))' % o)
            emit(o, ind)
            return
        if len(cand) <= 3:
            first = True
            for o in cand:
                w(ind + ('if' if first else 'elif') + ' op == %d:' % o)
                emit(o, ind + '    ')
                first = False
            w(ind + 'else: raise Halt("illegal opcode $%02X at $%04X" % (op, pc0))')
            return
        mid = (lo + hi) // 2
        w(ind + 'if op < %d:' % mid)
        tree(lo, mid, ind + '    ')
        w(ind + 'else:')
        tree(mid, hi, ind + '    ')

    def emit(o, ind):
        for line in _op_code(o):
            if line == '__TRAP__':
                w(ind + 'cpu.a, cpu.x, cpu.y, cpu.s, cpu.pc, cpu.c, cpu.zv, cpu.nv, cpu.v, cpu.d, cpu.i, cpu.cyc = '
                  'A, X, Y, S, pc0, C, zv, nv, V, D, I, cyc')
                w(ind + 'cpu.trap(pc0)')
                w(ind + ', '.join(_STATE) + ' = cpu.a, cpu.x, cpu.y, cpu.s, cpu.pc, cpu.c, cpu.zv, '
                  'cpu.nv, cpu.v, cpu.d, cpu.i, cpu.cyc')
            else:
                w(ind + line)

    tree(0, 256, '        ')
    w('        if prof:')
    w('            pcyc[pc0] += cyc - cy0')
    w('            pcnt[pc0] += 1')
    w('    finally:')
    w('      cpu.a, cpu.x, cpu.y, cpu.s, cpu.pc, cpu.c, cpu.zv, cpu.nv, cpu.v, cpu.d, cpu.i, cpu.cyc = '
      'A, X, Y, S, PC, C, zv, nv, V, D, I, cyc')
    src = '\n'.join(out)
    # I/O must see the CURRENT cycle (BUSY vs busy_until, the START time): hand the
    # local cyc to the machine before every rd/wr off a plain-RAM page
    src = src.replace('RD = cpu.rd; WR = cpu.wr', 'RD0 = cpu.rd; WR0 = cpu.wr')
    src = src.replace('RD(', '_io(cpu, cyc) or RD0(').replace('WR(', '_io(cpu, cyc) or WR0(')
    g = {'Halt': Halt, '_io': _io_sync}
    exec(compile(src, '<sim6502-generated>', 'exec'), g)
    return g['run'], src


# rename the state attribute spelling used by the generated code
_run, SOURCE = _gen()


class CPU:
    """State + memory; the machine subclass supplies rd/wr/trap/spin."""

    def __init__(self):
        self.m = bytearray(65536)
        self.rp = bytearray([1]) * 256          # 1 = plain RAM read page
        self.wp = bytearray([1]) * 256
        self.a = self.x = self.y = 0
        self.s = 0xFF
        self.pc = 0
        self.c = self.v = self.d = self.i = 0
        self.zv = 1
        self.nv = 0
        self.cyc = 0
        self.spins = set()
        self.pcyc = [0] * 65536
        self.pcnt = [0] * 65536                 # executions per pc (prof)
        self.prof = False

    # hooks (override)
    def rd(self, a):
        return self.m[a]

    def wr(self, a, v):
        self.m[a] = v

    def trap(self, pc):
        raise Halt('trap at $%04X' % pc)

    def spin(self, target, cyc):
        return 0

    def run(self, limit):
        _run(self, limit)

    # helpers for traps
    def push(self, v):
        self.m[256 + self.s] = v & 255
        self.s = (self.s - 1) & 255

    def pop(self):
        self.s = (self.s + 1) & 255
        return self.m[256 + self.s]

    def do_rts(self):
        lo = self.pop()
        hi = self.pop()
        self.pc = (((hi << 8) | lo) + 1) & 0xFFFF
        self.cyc += 6
