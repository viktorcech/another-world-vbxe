"""_verify_skillpass.py (skill pass 2026-09-09; baseline = _zal/base_20260909/awgame.xex + .lst) - run the OLD (baseline) and NEW awgame.xex on a cycle-counting 6502
and prove the skill-pass rewrites are output-identical:
  A) poly_draw on random shapes (fill + hier trees, zoom 64 and zoom!=64, on/off-screen,
     full and half detail) -> identical span stream (every BCB fire) + identical live state.
  B) op_condjmp / op_resettask on random operand streams -> identical VM state.
  C) emit_run (text) -> identical span + X preserved.
Also reports the cycle totals old vs new (the 'measured' half of the discipline).
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(__file__))
import _cpu6502 as cpu6502
import _smcvars as SV                                             # noqa: E402

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get('SKILLPASS_BASE', os.path.join(PROJ, '_zal', 'base_orig_20260909'))  # ORIGINAL (pre-skill-pass) build, rebuilt from the reconstructed sources in _zal/orig_20260909
OLD = (os.path.join(BASE, 'awgame.xex'), os.path.join(BASE, 'awgame.lst'))
NEW = (PROJ + r'\awgame.xex', PROJ + r'\out\awgame.lst')
OLD_INTRO = (os.path.join(BASE, 'awintro.xex'), os.path.join(BASE, 'awintro.lst'))
NEW_INTRO = (os.path.join(PROJ, 'awintro.xex'), os.path.join(PROJ, 'out', 'awintro.lst'))
SENT = 0xFFF0
BL = 0xD653


class Build:
    def __init__(self, xex, lst):
        self.img = bytearray(65536)
        cpu6502.load_xex(self.img, open(xex, 'rb').read())
        self.L = cpu6502.labels(lst)
        self.lst = lst
        self.cov = None                      # set() -> record every executed PC (--coverage)

    def fresh(self):
        mem = bytearray(self.img)
        cpu = cpu6502.CPU(mem)
        log = []
        def rd(a):
            if a == BL:
                return 0
            return None
        def wr(a, v):
            if a == BL and v == 1:
                log.append(bytes(mem[0x8100:0x8100 + 21]))
        cpu.rd_hook = rd; cpu.wr_hook = wr
        return cpu, mem, log

    def call(self, cpu, entry, stop=None, limit=5_000_000):
        cpu.sp = 0xFD
        r = (SENT - 1) & 0xFFFF
        cpu.push(r >> 8); cpu.push(r & 0xFF)
        cpu.pc = entry
        n = 0
        cov = self.cov
        while cpu.pc != SENT and cpu.pc != stop:
            if cov is not None:
                cov.add(cpu.pc)
            cpu.step(); n += 1
            if n > limit:
                raise RuntimeError('step limit at $%04X' % cpu.pc)
        return n


def w16(mem, a, v):
    mem[a] = v & 0xFF; mem[a + 1] = (v >> 8) & 0xFF


# ---------------------------------------------------------------- A) shapes
def gen_tree(rng, zoomed):
    """Return (stream bytes, list of child offsets) : a random shape tree at offset 0.
    10 % of the vertices fall outside their polygon's bbox (the clip paths' stress)."""
    buf = bytearray(0x3000)
    lim = 120 if zoomed else 255
    def poly(off, allow_dots):
        col = rng.randrange(0x40) | 0xC0
        n = rng.choice([1, 2] if allow_dots and rng.random() < 0.15 else [4, 4, 4, 6, 6, 8, 10, 12, 16])
        bbw = rng.randrange(0, lim); bbh = rng.randrange(0, lim)
        buf[off] = col; buf[off + 1] = bbw; buf[off + 2] = bbh; buf[off + 3] = n
        p = off + 4
        for i in range(n):
            buf[p] = rng.randrange(0, bbw + 1) if rng.random() < 0.9 else rng.randrange(lim)
            buf[p + 1] = rng.randrange(0, bbh + 1) if rng.random() < 0.9 else rng.randrange(lim)
            p += 2
        return p
    kids = []
    if rng.random() < 0.5:
        # hier node at 0, children at 0x100*k
        cnt = rng.randrange(0, 4)
        buf[0] = 0x02 | (rng.randrange(3) << 6)
        buf[1] = rng.randrange(lim); buf[2] = rng.randrange(lim); buf[3] = cnt
        p = 4
        for k in range(cnt + 1):
            coff = 0x100 * (k + 1)
            word = coff // 2
            colflag = rng.random() < 0.3
            if colflag:
                word |= 0x8000
            buf[p] = word >> 8; buf[p + 1] = word & 0xFF; p += 2
            buf[p] = rng.randrange(lim); buf[p + 1] = rng.randrange(lim); p += 2
            if colflag:
                buf[p] = rng.randrange(256); buf[p + 1] = rng.randrange(256); p += 2
            if rng.random() < 0.2 and k == 0:
                # nested hier at coff with one child polygon at coff+0x80
                buf[coff] = 0x02; buf[coff + 1] = rng.randrange(lim); buf[coff + 2] = rng.randrange(lim); buf[coff + 3] = 0
                w2 = (coff + 0x80) // 2
                buf[coff + 4] = w2 >> 8; buf[coff + 5] = w2 & 0xFF
                buf[coff + 6] = rng.randrange(lim); buf[coff + 7] = rng.randrange(lim)
                poly(coff + 0x80, True)
            else:
                poly(coff, True)
    else:
        poly(0, True)
    return bytes(buf)


SR_MODE = False                      # --sr : game build in the SR-320 mode of part 16008


def run_shape(b, stream, seed):
    rng = random.Random(seed)
    cpu, mem, log = b.fresh()
    L = b.L
    mem[0x4000:0x4000 + len(stream)] = stream
    zoom = rng.choice([64, 64, 64, 32, 48, 80, 128, 200, 300, 1000])
    for k, v in (('hires', 1 if SR_MODE else 0), ('poly_base_adj', 0), ('cc_baking', 0), ('cc_flag', 0)):
        if k in L:
            SV.poke(mem, L, k, v)           # game-only cells
    SV.poke(mem, L, 'poly_bcb_h', rng.randrange(2))
    w16(mem, L['dr_zoom'], zoom)
    w16(mem, L['dr_x'], rng.randrange(-40, 360) & 0xFFFF)
    w16(mem, L['dr_y'], rng.randrange(-40, 240) & 0xFFFF)
    mem[L['dr_col']] = rng.randrange(256)
    w16(mem, L['dr_off'], 0)
    mem[L['psp']] = 0
    mem[L['last_scol']] = 0xFF
    mem[L['memb_cur']] = 0
    if zoom == 64:
        w16(mem, L['rs_smc'] + 1, L['rs_fast'])
    elif 'rs_z4' in L:
        w16(mem, L['rs_smc'] + 1, L['rs_z4'])
        b.call(cpu, L['rs_z4_set'])
    else:
        w16(mem, L['rs_smc'] + 1, L['rs_slow'])   # intro: full mul_zoom path
    b.call(cpu, L['set_poly_ptr'])
    cpu.cyc = 0
    b.call(cpu, L['poly_draw'])
    state = bytes(mem[a:a + n] for a, n in ()) if False else b''
    keys = ['dr_x', 'dr_y', 'dr_col', 'dr_off', 'pb_ptr', 'poly_bnk', 'memb_cur', 'psp', 'scol', 'last_scol', 'sx_lo', 'sy', 'slen_lo', 'rpar']
    state = tuple((k, mem[L[k]], mem[L[k] + 1]) for k in keys if k in L)
    state += (bytes(mem[L['pts_xlo']:L['pts_xlo'] + 256]),)
    return log, state, cpu.cyc


def test_shapes(old, new, n):
    cyc_o = cyc_n = 0; spans = 0; fails = 0
    for seed in range(n):
        rng = random.Random(1000 + seed)
        stream = gen_tree(rng, rng.random() < 0.3)
        lo, so, co = run_shape(old, stream, seed)
        ln, sn, cn = run_shape(new, stream, seed)
        cyc_o += co; cyc_n += cn; spans += len(lo)
        if lo != ln or so != sn:
            fails += 1
            if fails < 5:
                print('  MISMATCH seed', seed, 'spans', len(lo), len(ln), 'state', so == sn)
                for i, (x, y) in enumerate(zip(lo, ln)):
                    if x != y:
                        print('   span', i, x.hex(), y.hex()); break
    print('A) shapes: %d trees, %d spans, mismatches=%d' % (n, spans, fails))
    print('   cycles old=%d new=%d  -> %.1f%% of old' % (cyc_o, cyc_n, 100.0 * cyc_n / cyc_o))
    return fails == 0


# ---------------------------------------------------------------- B) VM ops
SHAPE_AT = 0x4200        # a valid 4-vertex shape the draw opcodes are pointed at (off = $0200)
SHAPE = bytes([0xC5, 200, 150, 4, 180, 0, 200, 150, 0, 150, 20, 0])


def run_vmop(b, op, stream, seed, a=0):
    rng = random.Random(seed)
    cpu, mem, log = b.fresh()
    L = b.L
    mem[0x4000:0x4000 + len(stream)] = stream
    mem[SHAPE_AT:SHAPE_AT + len(SHAPE)] = SHAPE
    w16(mem, L['pl_wlo'], 0x4000)
    mem[L['pl_bank']] = 0x98; mem[L['memb_cur']] = 0x98
    w16(mem, L['pl_lo'], 0x0000)
    for i in range(256):
        mem[L['var_lo'] + i] = rng.randrange(256); mem[L['var_hi'] + i] = rng.choice([0, 0, 0xFF, rng.randrange(256)])
    for i in range(64):
        mem[L['treq_lo'] + i] = 0xFF; mem[L['treq_hi'] + i] = 0xFF; mem[L['tpreq'] + i] = 0xFF
        mem[L['vstk_lo'] + i] = rng.randrange(256); mem[L['vstk_hi'] + i] = rng.randrange(256)
    mem[L['req_any']] = 0
    mem[L['vm_ssp']] = rng.randrange(1, 60)
    SV.poke(mem, L, 'hires', 1)                     # SR: bypass the cell cache (unchanged code, deep state)
    SV.poke(mem, L, 'poly_bcb_h', rng.randrange(2))
    mem[L['last_scol']] = 0xFF
    mem[L['cc_baking']] = 0; mem[L['cc_flag']] = 0
    cpu.a = a; cpu.cyc = 0
    b.call(cpu, L[op], stop=L['vm_fetch'])
    # architectural state only: the raster's scratch cells moved between the builds
    # (RAMB+16..47 -> zp $C8-$CF / $88-$89), so those ranges are masked, as are the
    # VM's own scratch cells.
    zp = bytearray(mem[0x80:0x100]); ramb = bytearray(mem[0x9C00:0x9C80])
    zp[0x88 - 0x80] = zp[0x89 - 0x80] = 0
    for a in range(0xC0, 0xD4):
        zp[a - 0x80] = 0
    for a in range(0x9C10, 0x9C30):
        ramb[a - 0x9C00] = 0
    for k in ('vm_d', 'vm_dstlo', 'vm_dsthi', 'vm_op', 'vm_sub', 'vm_b2lo', 'vm_b2hi', 'vm_s1', 'vm_s2', 'tmp_lo', 'tmp_hi', 'a_lo', 'a_hi', 'b_lo', 'b_hi', 'zp_dlo', 'zp_dmid'):   # opcode-local scratch (vm_s1/s2 = the word-fetch pair, consumed inside the opcode)
        if k in L and 0x9C00 <= L[k] < 0x9C80:
            ramb[L[k] - 0x9C00] = 0
        if k in L and 0x80 <= L[k] < 0x100:
            zp[L[k] - 0x80] = 0
    st = (bytes(zp), bytes(ramb), bytes(mem[0xB000:0xB400]), cpu.pc == L['vm_fetch'], list(log))
    return st, cpu.cyc


VM_OPS = [  # (entry, runs, stream generator(rng) -> (bytes, A on entry))
    ('op_movconst', 200, lambda r: (bytes([r.randrange(256)] + [r.randrange(256) for _ in range(6)]), 0)),
    ('op_mov', 200, lambda r: (bytes([r.randrange(256) for _ in range(6)]), 0)),
    ('op_add', 200, lambda r: (bytes([r.randrange(256) for _ in range(6)]), 0)),
    ('op_addconst', 200, lambda r: (bytes([r.randrange(256) for _ in range(6)]), 0)),
    ('op_install', 200, lambda r: (bytes([r.randrange(64), r.randrange(256), r.randrange(256), 0, 0]), 0)),
    ('op_djnz', 200, lambda r: (bytes([r.randrange(256), r.randrange(0x30), r.randrange(256), 0, 0]), 0)),
    ('op_condjmp', 400, lambda r: (bytes([r.choice([0x00, 0x40, 0x80]) | r.randrange(8), r.randrange(256), r.randrange(256),
                                          r.randrange(256), r.randrange(0x30), r.randrange(256)] + [r.randrange(256) for _ in range(8)]), 0)),
    ('op_resettask', 200, lambda r: (bytes([r.randrange(64), r.randrange(64), r.randrange(4)] + [r.randrange(256) for _ in range(8)]), 0)),
    ('op_sub', 200, lambda r: (bytes([r.randrange(256) for _ in range(6)]), 0)),
    ('op_and', 200, lambda r: (bytes([r.randrange(256) for _ in range(6)]), 0)),
    ('op_or', 200, lambda r: (bytes([r.randrange(256) for _ in range(6)]), 0)),
    ('op_shl', 200, lambda r: (bytes([r.randrange(256) for _ in range(6)]), 0)),
    ('op_shr', 200, lambda r: (bytes([r.randrange(256) for _ in range(6)]), 0)),
    ('draw_bg', 120, lambda r: (bytes([0x00, r.randrange(256), r.choice([r.randrange(200), r.randrange(256)])] + [0] * 4), 0x81)),
    ('draw_sprite', 200, lambda r: (bytes([0x01, 0x00] + [r.randrange(256) for _ in range(6)]), 0x40 | r.randrange(0x40))),
]


def test_vm(old, new):
    ok = True
    print('B) VM opcodes (state + spans identical; cycles summed over the runs):')
    for op, n, gen in VM_OPS:
        co = cn = 0; fails = 0
        for seed in range(n):
            rng = random.Random(5000 + seed)
            stream, a = gen(rng)
            so, o = run_vmop(old, op, stream, seed, a); sn, nn = run_vmop(new, op, stream, seed, a)
            co += o; cn += nn
            if so != sn:
                fails += 1
        print('   %-14s %4d runs, mismatches=%d, cycles old=%8d new=%8d (%+.1f%%)' % (op, n, fails, co, cn, 100.0 * (cn - co) / co))
        ok = ok and fails == 0
    return ok


# ---------------------------------------------------------------- C) emit_run
def test_text(old, new):
    fails = 0; co = cn = 0
    for seed in range(100):
        res = []
        for b in (old, new):
            rng = random.Random(7000 + seed)
            cpu, mem, log = b.fresh(); L = b.L
            SV.poke(mem, L, 'hires', 0)
            w16(mem, L['t_cbx'], rng.randrange(0, 312))
            i0 = rng.randrange(0, 8); x = rng.randrange(i0 + 1, 9)
            mem[L['t_i0']] = i0; mem[L['sy']] = rng.randrange(200); mem[L['scol']] = rng.randrange(16)
            mem[L['last_scol']] = 0xFF
            if 'span_mode' in L:            # what do_drawstring does before its runs:
                cpu.a = mem[L['scol']]      #   the colour's mode fields once, and the
                b.call(cpu, L['span_mode']) #   fused span the runs then take
                for nm in ('draw_scanline_fast.dsf_m', 'dsf_m'):
                    if nm in L:
                        mem[L[nm]] = 0xD0
                        break
            cpu.x = x; cpu.cyc = 0
            b.call(cpu, L['emit_run'])
            # (sx / slen: span parameters the span itself consumes -- the fused path
            #  writes the BCB straight, and the log above is that BCB at the START)
            res.append((list(log), cpu.x))
            if b is old: co += cpu.cyc
            else: cn += cpu.cyc
        if res[0] != res[1]:
            fails += 1
    print('C) emit_run: 100 runs, mismatches=%d, cycles old=%d new=%d' % (fails, co, cn))
    return fails == 0


HOT_PROCS = ['fill_poly_int', 'adv_edges1', 'draw_scanline', 'draw_scanline_yok', 'draw_scanline_fast',
             'emit_span', 'fill_span', 'calc_step', 'poly_draw', 'do_fill', 'do_hier', 'rs_fast', 'rs_z4',
             'rs_z4_set', 'set_poly_ptr', 'pf_wrap', 'mul_zoom', 'rs_slow', 'poly_fetch']


def coverage_report(b):
    """List the instructions of the hot procs that the random-shape run NEVER executed
    (the _an_waste idea from the skill: dynamically dead code is a hint, not a proof --
    check the path the test never took before deleting anything)."""
    import re
    cur = None; dead = {}; total = {}
    for ln in open(b.lst, encoding='utf-8', errors='replace'):
        m = re.match(r'^\s*\d+\s+([0-9A-F]{4})(?:\s[0-9A-F]{2})*	+(.*)$', ln)
        if not m:
            continue
        src = m.group(2).rstrip()
        if not src.strip():
            continue
        mm = re.match(r'^\.proc\s+([A-Za-z_]\w*)', src)
        if mm:
            cur = mm.group(1) if mm.group(1) in HOT_PROCS else None; continue
        if src.strip().startswith('.endp'):
            cur = None; continue
        if cur is None or src.lstrip().startswith(';'):
            continue
        has_bytes = re.match(r'^\s*\d+\s+[0-9A-F]{4}\s[0-9A-F]{2}', ln) is not None
        if not has_bytes or re.search(r'(dta|equ|ert)', src.split(';')[0]):
            continue
        addr = int(m.group(1), 16)
        total[cur] = total.get(cur, 0) + 1
        if addr not in b.cov:
            dead.setdefault(cur, []).append((addr, src.strip()))
    print('D) coverage of the hot procs (instructions never executed by the shape run):')
    for pn in HOT_PROCS:
        if pn not in total:
            continue
        d = dead.get(pn, [])
        print('   %-20s %3d/%3d never hit' % (pn, len(d), total[pn]))
        for addr, src in d:
            print('        $%04X  %s' % (addr, src[:70]))


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    n = int(args[0]) if args else 300
    if '--intro' in sys.argv:
        print('== INTRO build (awintro.xex) ==')
        old = Build(*OLD_INTRO); new = Build(*NEW_INTRO)
        if '--coverage' in sys.argv:
            new.cov = set()
        ok = test_shapes(old, new, n)
    else:
        print('== GAME build (awgame.xex) ==')
        old = Build(*OLD); new = Build(*NEW)
        if '--coverage' in sys.argv:
            new.cov = set()
        ok = test_shapes(old, new, n)
        SR_MODE = True                                   # the SR-320 paths (part 16008)
        print('   (again in SR-320 mode, hires = 1)')
        ok = test_shapes(old, new, n) and ok
        SR_MODE = False
        ok = test_vm(old, new) and ok
        ok = test_text(old, new) and ok
    if new.cov is not None:
        coverage_report(new)
    print('RESULT:', 'OK - bit-identical' if ok else 'FAILED')
    sys.exit(0 if ok else 1)
