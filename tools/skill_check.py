#!/usr/bin/env python3
"""skill_check -- the skill-pass rewrites of the game checked against the ORIGINAL
behaviour, on the real assembled build (awgame.xex + out/awgame.lst), with the
cycle-exact 6502 core. Seconds. Each check calls one routine with random inputs and
compares what the hardware would SEE (the BCB a START hands the blitter, the XDL / BCB
template bytes, the pointers) with a Python transcription of the old code.

    python tools/skill_check.py [N]          (N random cases per check, default 400)
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _cpu6502 as C                                              # noqa: E402

SENT = 0xFFF0
BUSY = 0xD653
SCRW, SCRH = 160, 200
ROWBIAS = 0x4000
MEMW = 0x8000


class Build:
    def __init__(self):
        self.img = bytearray(65536)
        C.load_xex(self.img, open(os.path.join(PROJ, 'awgame.xex'), 'rb').read())
        self.L = C.labels(os.path.join(PROJ, 'out', 'awgame.lst'))

    def fresh(self):
        mem = bytearray(self.img)
        cpu = C.CPU(mem)
        st = {'adr0': 0, 'starts': [], 'xdla0': None, 'pal': []}

        def rd(a):
            if a == BUSY:
                return 0
            return None

        def wr(a, v):
            if a == 0xD650:
                st['adr0'] = v
            elif a == BUSY and v & 1:
                base = 0x8100 + st['adr0']
                lst = []
                for _ in range(4):
                    b = bytes(mem[base:base + 21])
                    lst.append(b)
                    base += 21
                    if not b[20] & 8:
                        break
                st['starts'].append(lst)
            elif a == 0xD641:
                st['xdla0'] = v
        cpu.rd_hook = rd
        cpu.wr_hook = wr
        return cpu, mem, st

    def call(self, cpu, entry, limit=2_000_000):
        cpu.sp = 0xFD
        r = (SENT - 1) & 0xFFFF
        cpu.push(r >> 8)
        cpu.push(r & 0xFF)
        cpu.pc = entry
        n = 0
        while cpu.pc != SENT:
            cpu.step()
            n += 1
            if n > limit:
                raise RuntimeError('step limit at $%04X' % cpu.pc)


def w16(mem, a, v):
    mem[a] = v & 0xFF
    mem[a + 1] = (v >> 8) & 0xFF


def row_off(y):
    return (y * SCRW - ROWBIAS) & 0xFFFF


FAILS = []


def check(name, ok, detail=''):
    if not ok:
        FAILS.append((name, detail))
        if sum(1 for f in FAILS if f[0] == name) <= 3:
            print('   FAIL', name, detail)


# ------------------------------------------------------------------ fill_span
def t_fill_span(b, n):
    L = b.L
    rng = random.Random(11)
    cpu, mem, st = b.fresh()
    b.call(cpu, L['upload_bcb'])
    mem[L['hires']] = 0
    mem[L['last_scol']] = 0xFF
    for k in range(n):
        if rng.random() < 0.08:                  # some other BCB user clobbered the span BCB
            for f in (2, 5, 13, 15, 16, 20):
                mem[0x8100 + f] = rng.randrange(256)
            mem[L['last_scol']] = 0xFF
        scol = rng.choice([rng.randrange(16), rng.randrange(16), 0x10, 0x11, 0x11, rng.randrange(0x11, 0x80)])
        sy = rng.randrange(200)
        sx = 0x4000 + rng.randrange(160)
        slen = rng.randrange(0, 160 - (sx - 0x4000))
        mem[L['scol']] = scol
        mem[L['sy']] = sy
        w16(mem, L['sx_lo'], sx)
        mem[L['slen_lo']] = slen
        mem[L['slen_hi']] = 0
        st['starts'].clear()
        b.call(cpu, L['fill_span'])
        check('fill_span one START', len(st['starts']) == 1)
        if not st['starts']:
            continue
        bcb = st['starts'][0][0]
        off = (row_off(sy) + sx) & 0xFFFF
        exp_dst = (off & 0xFF, off >> 8)
        check('fill_span DST', (bcb[6], bcb[7]) == exp_dst, (k, bcb.hex(), hex(off)))
        check('fill_span WIDTH', bcb[12] == slen and bcb[13] == 0, (k, bcb.hex(), slen))
        if scol >= 0x11:
            check('fill_span copy', bcb[0] == exp_dst[0] and bcb[1] == exp_dst[1] and bcb[2] == 0
                  and bcb[5] == 1 and bcb[15] == 0xFF and bcb[16] == 0 and bcb[20] & 7 == 0, (k, bcb.hex()))
        elif scol == 0x10:
            check('fill_span transp', bcb[15] == 0 and bcb[16] == 8 and bcb[20] & 7 == 3, (k, bcb.hex()))
        else:
            check('fill_span solid', bcb[15] == 0 and bcb[16] == scol and bcb[20] & 7 == 0, (k, bcb.hex()))
        check('fill_span no chain', not bcb[20] & 8)


# ------------------------------------------------------------------ cc_blit
def cc_blit_ref(ent, dr_x, dr_y, page):
    """the old cc_blit's clip + geometry (game_cellcache.asm .else branch), or None."""
    w = ent[9] + 1
    h = ent[10] + 1
    ax = ent[11] - 256 if ent[11] & 0x80 else ent[11]
    ay = ent[12] - 256 if ent[12] & 0x80 else ent[12]
    dx16 = ((dr_x - (dr_x & 1)) & 0xFFFF)
    dx16 = dx16 - 0x10000 if dx16 & 0x8000 else dx16
    dx = (dx16 >> 1) + ax
    dy = dr_y + ay
    dx &= 0xFFFF
    dy &= 0xFFFF
    sk = 0
    bw, bh = w, h
    if dx & 0x8000:
        skip = (0 - (dx & 0xFF)) & 0xFF
        if skip >= bw:
            return None
        sk = skip
        bw -= skip
        dx = 0
    if dx >> 8:
        return None
    if (dx & 0xFF) >= SCRW:
        return None
    s = (dx & 0xFF) + bw
    if s > 255 or s >= SCRW + 1:
        bw = SCRW - dx
    if dy & 0x8000:
        skip = (0 - (dy & 0xFF)) & 0xFF
        if skip >= bh:
            return None
        bh -= skip
        sk += skip * w
        dy = 0
    if dy >> 8:
        return None
    if dy >= SCRH:
        return None
    s = dy + bh
    if s > 255 or s >= SCRH + 1:
        bh = SCRH - dy
    cell = ent[6] | (ent[7] << 8) | (ent[8] << 16)
    src = (cell + sk) & 0xFFFFFF
    if ((cell & 0xFFFF) + (sk & 0xFFFF)) > 0xFFFF:
        pass
    off = (row_off(dy) + dx + ROWBIAS) & 0xFFFF
    return dict(src=src, sstep=w, dst=off | (page << 16), width=(bw - 1) & 0xFF, height=(bh - 1) & 0xFF)


def t_cc_blit(b, n):
    L = b.L
    rng = random.Random(22)
    cpu, mem, st = b.fresh()
    b.call(cpu, L['upload_bcb'])
    b.call(cpu, L['cc_tmpl_init'])
    mem[L['cc_lst']] = 0
    span_before = bytes(mem[0x8100:0x8115])
    ent_at = 0x7000
    w16(mem, L['cc_ptr'], ent_at)
    for k in range(n):
        w = rng.randrange(1, 161)
        h = rng.randrange(1, 201)
        ent = [2, 0, 0, 64, 0, 0, rng.randrange(256), rng.randrange(256), rng.randrange(8),
               w - 1, h - 1, rng.randrange(256), rng.randrange(256)]
        mem[ent_at:ent_at + 13] = bytes(ent)
        dr_x = rng.choice([rng.randrange(-60, 380), rng.randrange(0, 320), rng.randrange(-700, 900)]) & 0xFFFF
        dr_y = rng.choice([rng.randrange(-60, 260), rng.randrange(0, 200), rng.randrange(-500, 700)]) & 0xFFFF
        page = rng.randrange(4)
        w16(mem, L['dr_x'], dr_x)
        w16(mem, L['dr_y'], dr_y)
        mem[L['cbase'] + 2] = page
        st['starts'].clear()
        b.call(cpu, L['cc_blit'])
        exp = cc_blit_ref(ent, dr_x, dr_y, page)
        if exp is None:
            check('cc_blit clipped out -> no START', not st['starts'], (k, ent, dr_x, dr_y))
            continue
        check('cc_blit one START', len(st['starts']) == 1, (k, ent, dr_x, dr_y))
        if not st['starts']:
            continue
        lst = st['starts'][0]
        check('cc_blit chain of 2', len(lst) == 2, (k, [x.hex() for x in lst]))
        if len(lst) != 2:
            continue
        for i, bcb in enumerate(lst):
            src = bcb[0] | (bcb[1] << 8) | (bcb[2] << 16)
            dst = bcb[6] | (bcb[7] << 8) | (bcb[8] << 16)
            got = dict(src=src, sstep=bcb[3] | (bcb[4] << 8), dst=dst, width=bcb[12], height=bcb[14])
            check('cc_blit geometry', got == exp, (k, i, got, exp))
            check('cc_blit consts', bcb[5] == 1 and bcb[9] == 160 and bcb[10] == 0 and bcb[11] == 1
                  and bcb[13] == 0 and bcb[16] == 0 and bcb[17] == 0 and bcb[18] == 0 and bcb[19] == 0,
                  (k, i, bcb.hex()))
        check('cc_blit pass 1', lst[0][15] == 0xF0 and lst[0][20] == 0x09, lst[0].hex())
        check('cc_blit pass 2', lst[1][15] == 0xFF and lst[1][20] == 0x05, lst[1].hex())
        check('cc_blit BL_ADR back to the span BCB', st['adr0'] == 0)
    check('cc_blit leaves the span BCB alone', bytes(mem[0x8100:0x8115]) == span_before)


# ------------------------------------------------------------------ pages / xdl / palette
def full_page_bcb(kind, a, b_):
    if kind == 'clear':
        return bytes([0, 0, 0, 0, 0, 0, 0, 0, a, SCRW, 0, 1, SCRW - 1, 0, SCRH - 1, 0, b_, 0, 0, 0, 0])
    return bytes([0, 0, a, SCRW, 0, 1, 0, 0, b_, SCRW, 0, 1, SCRW - 1, 0, SCRH - 1, 0xFF, 0, 0, 0, 0, 0])


def t_pages(b):
    L = b.L
    cpu, mem, st = b.fresh()
    b.call(cpu, L['upload_bcb'])
    mem[L['hires']] = 0
    for page in range(4):
        for col in (0, 5, 15):
            st['starts'].clear()
            cpu.a, cpu.x = page, col
            b.call(cpu, L['clear_page'])
            got = st['starts'][0][0] if st['starts'] else None
            exp = full_page_bcb('clear', page, col)
            # AND = 0: the source fields are never read -- compare what the blit does
            check('clear_page BCB', got is not None and got[6:] == exp[6:], (page, col, got and got.hex()))
    for s_ in range(4):
        for d in range(4):
            st['starts'].clear()
            mem[L['cp_src']] = s_
            mem[L['cp_dst']] = d
            b.call(cpu, L['copy_page'])
            got = st['starts'][0][0] if st['starts'] else None
            check('copy_page BCB', got == full_page_bcb('copy', s_, d), (s_, d, got and got.hex()))
    check('page blits leave BL_ADR at the span BCB', st['adr0'] == 0)
    for page in range(4):
        cpu.a = page
        b.call(cpu, L['show_page'])
        check('show_page XDLA0', st['xdla0'] == page * 0x40, (page, st['xdla0']))
    b.call(cpu, L['setup_xdls'])
    tm = L['xdl_tmpl']
    for p in range(4):
        x = bytes(mem[MEMW + p * 0x40:MEMW + p * 0x40 + 13])
        exp = bytearray(mem[tm:tm + 13])
        exp[8] = p
        check('setup_xdls XDL', x == bytes(exp), (p, x.hex(), bytes(exp).hex()))
    for n in range(32):
        cpu.a = n
        b.call(cpu, L['set_palette'])
        ptr = mem[L['pal_ptr']] | (mem[L['pal_ptr'] + 1] << 8)
        check('set_palette pointer', ptr == L['pal_data'] + 48 * n, (n, hex(ptr)))


def t_render_mode(b):
    L = b.L
    cpu, mem, st = b.fresh()
    b.call(cpu, L['upload_bcb'])
    b.call(cpu, L['setup_xdls'])
    mem[L['cpu_detail']] = 1
    for mode in (1, 0, 1, 0):
        cpu.a = mode
        b.call(cpu, L['set_render_mode'])
        w = 320 if mode else 160
        px = (0x08 | 0x80) if mode else (0x08 | 0x80 | 0x20)
        for p in range(4):
            o = MEMW + p * 0x40
            check('set_render_mode XDL', mem[o + 4] == px and mem[o + 9] == w & 0xFF and mem[o + 10] == w >> 8,
                  (mode, p, mem[o + 4], mem[o + 9], mem[o + 10]))
        check('set_render_mode detail', mem[L['poly_bcb_h']] == (0 if mode else 1))
        P, K = 0x8115, 0x812A
        check('set_render_mode clear tmpl', mem[P + 9] | (mem[P + 10] << 8) == w and
              mem[P + 12] | (mem[P + 13] << 8) == w - 1)
        check('set_render_mode copy tmpl', mem[K + 3] | (mem[K + 4] << 8) == w and
              mem[K + 9] | (mem[K + 10] << 8) == w and mem[K + 12] | (mem[K + 13] << 8) == w - 1)
        fs = L['fill_span_sr'] if mode else L['fill_span']
        a1 = L['cc_fsp'] if 'cc_fsp' in L else None
        for site in ('cc_fsp', 'cc_dds'):
            if site in L:
                v = mem[L[site] + 1] | (mem[L[site] + 2] << 8)
                check('set_render_mode span dispatch', v == fs, (site, hex(v), hex(fs)))
        check('set_render_mode invalidates last_scol', mem[L['last_scol']] == 0xFF)


def t_cc_lookup(b, n):
    """cc_lookup: the key cc_key+0..4, the slot pointer and the state it returns
    (the entry's state when its 5 key bytes match, else 0), as the old code built them."""
    L = b.L
    rng = random.Random(44)
    cpu, mem, st = b.fresh()
    for k in range(n):
        off = rng.randrange(0x10000)
        zoom = rng.choice([64, 64, rng.randrange(0x10000)])
        x = rng.randrange(0x10000)
        adj = rng.choice([0, 8])
        w16(mem, L['dr_off'], off)
        w16(mem, L['dr_zoom'], zoom)
        w16(mem, L['dr_x'], x)
        mem[L['poly_base_adj']] = adj
        key = [off & 0xFF, off >> 8, zoom & 0xFF, zoom >> 8, (x & 1) | (2 if adj else 0)]
        slot = (key[0] ^ key[2] ^ key[1] ^ key[3]) & 0xFF | (((key[1] ^ key[3] ^ key[4]) & 1) << 8)
        ptr = 0x4000 + slot * 16
        state = rng.choice([0, 1, 2, 3])
        match = rng.random() < 0.6
        ent = [state] + (key if match else [rng.randrange(256) for _ in range(5)])
        mem[ptr:ptr + 6] = bytes(ent)
        b.call(cpu, L['cc_lookup'])
        got_ptr = mem[L['cc_ptr']] | (mem[L['cc_ptr'] + 1] << 8)
        exp = 0 if state == 0 else (state if ent[1:6] == key else 0)
        check('cc_lookup key', bytes(mem[L['cc_key']:L['cc_key'] + 5]) == bytes(key), (k, key))
        check('cc_lookup slot', got_ptr == ptr, (k, hex(got_ptr), hex(ptr)))
        check('cc_lookup state', cpu.a == exp, (k, cpu.a, exp))
        check('cc_lookup bank', mem[L['memb_cur']] == 0x86)


def t_input(b):
    """vm_update_input, exhaustive: every PORTA x TRIG0 against the old branch chain."""
    L = b.L
    VL, VH = 0xB000, 0xB100
    for porta in range(256):
        for trig in (0, 1):
            cpu, mem, st = b.fresh()
            rd0 = cpu.rd_hook
            cpu.rd_hook = lambda a, p=porta, t=trig, r=rd0: p if a == 0xD300 else (t if a == 0xD010 else r(a))
            b.call(cpu, L['vm_update_input'])
            lr = ud = m = 0
            if not porta & 8:
                lr, m = 1, m | 1
            if not porta & 4:
                lr, m = -1, m | 2
            if not porta & 2:
                ud, m = 1, m | 4
            if not porta & 1:
                ud, m = -1, m | 8
            act = 0 if trig & 1 else 1
            exp = {0xFC: lr & 0xFFFF, 0xE5: ud & 0xFFFF, 0xFB: ud & 0xFFFF, 0xFA: act, 0xFD: m,
                   0xFE: m | (0x80 if act else 0)}
            for k, e in exp.items():
                check('vm_update_input', mem[VL + k] | (mem[VH + k] << 8) == e, (porta, trig, hex(k)))
            check('vm_update_input ATRACT', mem[0x4D] == 0)


def t_text(b):
    """set_t_cbx (t_cbx = cx*8) and draw_glyph's font pointer (aw_font + (ch-$20)*8),
    every byte value, against the old 16-bit shifts."""
    L = b.L
    cpu, mem, st = b.fresh()
    b.call(cpu, L['upload_bcb'])
    for v in range(256):
        mem[L['t_cx']] = v
        b.call(cpu, L['set_t_cbx'])
        got = mem[L['t_cbx']] | (mem[L['t_cbx'] + 1] << 8)
        check('set_t_cbx', got == (v * 8) & 0xFFFF, (v, hex(got)))
        mem[L['t_ch']] = v
        mem[L['txt_y']] = 250                    # every row off the page: no spans
        b.call(cpu, L['draw_glyph'])
        got = mem[L['t_fp']] | (mem[L['t_fp'] + 1] << 8)
        check('draw_glyph font ptr', got == (L['aw_font'] + ((v - 0x20) & 0xFF) * 8) & 0xFFFF, (v, hex(got)))


def t_arena(b, n):
    L = b.L
    rng = random.Random(33)
    cpu, mem, st = b.fresh()
    for k in range(n):
        cnt = rng.randrange(0, 512)
        top = rng.choice([0x0000, 0x8000])
        reg = rng.choice([5, 6, 7])
        y = rng.randrange(2, 5)
        w16(mem, L['cc_t0'], cnt)
        w16(mem, L['cc_t1'], top)
        cpu.a, cpu.y = reg, y
        b.call(cpu, L['cc_set_arena'])
        base = (reg << 16) + cnt * 128
        size = (top - cnt * 128) & 0xFFFF
        got_b = mem[0x9E80 + y] | (mem[0x9E85 + y] << 8) | (mem[0x9E8A + y] << 16)
        got_s = mem[0x9E8F + y] | (mem[0x9E94 + y] << 8)
        check('cc_set_arena', got_b == base and got_s == size, (cnt, top, reg, hex(got_b), hex(got_s)))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    b = Build()
    L = b.L
    # the harness labels
    for k in ('cc_fsp', 'cc_dds'):
        pass
    t_fill_span(b, n)
    t_cc_blit(b, n)
    t_pages(b)
    t_render_mode(b)
    t_arena(b, n)
    t_cc_lookup(b, n)
    t_input(b)
    t_text(b)
    names = sorted(set(f[0] for f in FAILS))
    if FAILS:
        print('FAILED: %d checks in %s' % (len(FAILS), ', '.join(names)))
        sys.exit(1)
    print('skill_check: all OK (%d cases per random check)' % n)


if __name__ == '__main__':
    main()
