#!/usr/bin/env python3
"""skill_measure -- cycle micro-benchmarks of the game's hot paths on the cycle-exact
NMOS 6502 core (tools/_cpu6502.py), for before/after numbers of a skill pass. Seconds,
not minutes: every case calls one routine of the REAL assembled build (awgame.xex +
out/awgame.lst) with fixed inputs.

The blitter is modelled for the waits (vbxe-blitter skill): a START runs the BCB list
(BL_ADR, BLT_NEXT chains) and BUSY stays set for its cost in blitter cycles -- 21 a BCB
(+1 when AND = 0), per row dst [+src when AND != 0][+dst for RMW modes], as
alt-src/Altirra/source/vbxe.cpp prices it -- at 7.1 blitter cycles per CPU cycle.
Source bytes are taken as non-zero (no zero-skip credit): a fixed worst case, the same
for both builds.

    python tools/skill_measure.py [--save NAME] [--cmp NAME]
      --save NAME : write out/measure_NAME.txt     --cmp NAME : print old -> new
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _cpu6502 as C                                              # noqa: E402
import _smcvars as SV                                             # noqa: E402

RATE = (312 * 912 - 200 * 160) / (312 * 114)
SENT = 0xFFF0
BUSY = 0xD653


class B:
    def __init__(self):
        xex = os.path.join(PROJ, 'awgame.xex')
        lst = os.path.join(PROJ, 'out', 'awgame.lst')
        self.img = bytearray(65536)
        C.load_xex(self.img, open(xex, 'rb').read())
        self.L = C.labels(lst)

    def fresh(self):
        mem = bytearray(self.img)
        cpu = C.CPU(mem)
        st = {'busy_until': 0, 'adr0': 0x00, 'starts': 0, 'blit': 0}

        def cost_bcb(a):
            b = mem[a:a + 21]
            w = b[12] + ((b[13] & 1) << 8) + 1
            h = b[14] + 1
            andm, xorm, mode = b[15], b[16], b[20] & 7
            cpr = w
            if andm:
                cpr += w
            if (andm or xorm) and mode not in (0, 1):
                cpr += w
            if mode == 1 and b[17] and (andm or xorm):
                cpr += w
            return 21 + (0 if andm else 1) + h * cpr, b[20] & 8

        def rd(a):
            if a == BUSY:
                return 2 if cpu.cyc < st['busy_until'] else 0
            return None

        def wr(a, v):
            if a == 0xD650:
                st['adr0'] = v
            elif a == BUSY and v & 1:
                if cpu.cyc < st['busy_until']:
                    return                               # START while busy: ignored
                a0 = 0x8000 + 0x100 + ((st['adr0'] - 0x00) & 0xFF) - 0x00
                a0 = 0x8100 + st['adr0']                  # the $0401xx page, CPU window
                tot = 0
                for _ in range(8):
                    c, more = cost_bcb(a0)
                    tot += c
                    a0 += 21
                    if not more:
                        break
                st['starts'] += 1
                st['blit'] += tot
                st['busy_until'] = cpu.cyc + int(tot / RATE + 0.999)
        cpu.rd_hook = rd
        cpu.wr_hook = wr
        return cpu, mem, st

    def call(self, cpu, entry, limit=3_000_000):
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


def shape(col, bbw, bbh, pts):
    b = bytearray([0xC0 | col, bbw, bbh, len(pts)])
    for x, y in pts:
        b += bytes([x, y])
    return bytes(b)


POLY = [
    ('poly big trapezoid, full detail', shape(5, 200, 150, [(180, 0), (200, 150), (0, 150), (20, 0)]), 160, 100, 64, 0),
    ('poly big trapezoid, half detail', shape(5, 200, 150, [(180, 0), (200, 150), (0, 150), (20, 0)]), 160, 100, 64, 1),
    ('poly tall quad x-clipped, half', shape(7, 120, 180, [(120, 0), (120, 180), (0, 180), (0, 0)]), 30, 100, 64, 1),
    ('poly tall quad y-clipped, half', shape(7, 120, 180, [(120, 0), (120, 180), (0, 180), (0, 0)]), 160, 40, 64, 1),
    ('poly sprite 8 verts, half', shape(3, 24, 30, [(14, 0), (24, 12), (23, 20), (22, 30), (2, 30), (0, 12), (4, 2), (10, 0)]), 160, 100, 64, 1),
    ('poly sprite 8 verts zoom 128, half', shape(3, 24, 30, [(14, 0), (24, 12), (23, 20), (22, 30), (2, 30), (0, 12), (4, 2), (10, 0)]), 160, 100, 128, 1),
    ('poly copy-mode quad (col $11), half', shape(0x11, 100, 80, [(100, 0), (100, 80), (0, 80), (0, 0)]), 160, 100, 64, 1),
]


def m_poly(b, st_, dx, dy, zoom, half):
    L = b.L
    cpu, mem, st = b.fresh()
    mem[0x4000:0x4000 + len(st_)] = st_
    for k in ('hires', 'poly_base_adj', 'cc_baking', 'cc_flag'):
        if k in L:
            SV.poke(mem, L, k, 0)
    SV.poke(mem, L, 'poly_bcb_h', half)
    w16(mem, L['dr_zoom'], zoom)
    w16(mem, L['dr_x'], dx)
    w16(mem, L['dr_y'], dy)
    mem[L['dr_col']] = 0xFF
    w16(mem, L['dr_off'], 0)
    mem[L['psp']] = 0
    mem[L['last_scol']] = 0xFF
    mem[L['memb_cur']] = 0
    if zoom == 64:
        w16(mem, L['rs_smc'] + 1, L['rs_fast'])
    else:
        w16(mem, L['rs_smc'] + 1, L['rs_z4'])
        b.call(cpu, L['rs_z4_set'])
    b.call(cpu, L['set_poly_ptr'])
    cpu.cyc = 0
    b.call(cpu, L['poly_draw'])
    return cpu.cyc, st['starts'], st['blit']


def m_spans(b, mode):
    """100 spans through emit_span's fill_span dispatch, the blitter idle each time."""
    L = b.L
    cpu, mem, st = b.fresh()
    SV.poke(mem, L, 'hires', 0)
    mem[L['last_scol']] = 0xFF
    tot = 0
    rng = random.Random(1)
    for k in range(100):
        col = {'solid': 5, 'copy': 0x11, 'alt': 5 if k & 1 else 6}[mode]
        mem[L['scol']] = col
        mem[L['sy']] = rng.randrange(200)
        w16(mem, L['sx_lo'], 0x4000 + rng.randrange(100))
        mem[L['slen_lo']] = rng.randrange(60)
        mem[L['slen_hi']] = 0
        st['busy_until'] = 0
        cpu.cyc = 0
        b.call(cpu, L['fill_span'])
        tot += cpu.cyc
    return tot / 100.0


def m_irq(b, phase):
    L = b.L
    cpu, mem, st = b.fresh()
    if 'snd_irq' not in L:
        return None
    base = L['snd_irq']
    act = L.get('snd_active', 0xAB)
    # the two encodings: old = 1 / 2, new (bit test) = $80 / $40 -- read which one the
    # build uses from snd_play's `lda #imm / sta snd_active`
    v1 = 1
    for a in range(L['snd_play'], L['snd_play'] + 80):
        if mem[a] == 0xA9 and mem[a + 2] in (0x85,) and mem[a + 3] == 0xAB:
            v1 = mem[a + 1]
            break
    v2 = 2 if v1 == 1 else 0x40
    mem[0xAB] = v1 if phase == 0 else v2
    if 'sph1' in L and 'body' in L:             # the state = the low byte of `body jmp`
        mem[L['body'] + 1] = (L['ph0'] if phase == 0 else L['sph1']) & 0xFF
    mem[0xAD] = 0x5A
    mem[0xB4] = 0x8E
    mem[0x10] = 0xC1                            # POKMSK with Timer 1 on
    rdh = cpu.rd_hook

    def rd(a):
        if a == 0xD20E:
            return 0xFE                         # IRQST: Timer 1 pending (bit 0 = 0)
        return rdh(a)
    cpu.rd_hook = rd
    w16(mem, L['snd_rem'], 0xFF00)
    # IRQ entry: the OS has pushed PC/P and jumped via VIMIRQ; we push a fake frame
    cpu.sp = 0xFD
    r = SENT
    cpu.push(r >> 8)
    cpu.push(r & 0xFF)
    cpu.push(0x20)
    cpu.pc = base
    cpu.cyc = 0
    n = 0
    while cpu.pc != SENT and n < 200:
        cpu.step()
        n += 1
    return cpu.cyc


def m_ccblit(b, w, h):
    """one cache hit: cc_blit of a w x h cell at mid-screen. Returns (CPU cycles spent
    inside cc_blit incl. its blitter waits, blitter cycles still pending at return)."""
    L = b.L
    if 'cc_blit' not in L:
        return None
    cpu, mem, st = b.fresh()
    ent = 0x7000
    w16(mem, L['cc_ptr'], ent)
    e = [2, 0, 0, 64, 0, 0, 0x00, 0x90, 0x00, w - 1, h - 1, (256 - w // 2) & 0xFF, (256 - h // 2) & 0xFF]
    mem[ent:ent + len(e)] = bytes(e)
    w16(mem, L['dr_x'], 160)
    w16(mem, L['dr_y'], 100)
    mem[L['cbase'] + 2] = 1
    mem[L['last_scol']] = 5
    cpu.cyc = 0
    b.call(cpu, L['cc_blit'])
    left = max(0, st['busy_until'] - cpu.cyc)
    return cpu.cyc, left


def m_page(b, which):
    L = b.L
    cpu, mem, st = b.fresh()
    SV.poke(mem, L, 'hires', 0)
    if which == 'clear':
        cpu.a, cpu.x = 2, 0
        cpu.cyc = 0
        b.call(cpu, L['clear_page'])
    else:
        mem[L['cp_src']] = 0
        mem[L['cp_dst']] = 2
        cpu.cyc = 0
        b.call(cpu, L['copy_page'])
    return cpu.cyc


def measure():
    b = B()
    res = []
    for name, st_, dx, dy, z, h in POLY:
        c, n, bl = m_poly(b, st_, dx, dy, z, h)
        # the blitter's own budget (vbxe-blitter skill): 8 x 114 cycles a scan line
        # minus the display's DMA -- about 250 thousand a PAL frame
        res.append((name, c, '%d spans, blitter %d cyc = %.1f%% of a frame'
                    % (n, bl, 100.0 * bl / 250000)))
    for mode in ('solid', 'alt', 'copy'):
        res.append(('fill_span x100 avg, %s' % mode, m_spans(b, mode), ''))
    for ph in (0, 1):
        v = m_irq(b, ph)
        if v is not None:
            res.append(('snd_irq phase %d' % ph, v, ''))
    for w, h in ((16, 16), (32, 40), (64, 64)):
        v = m_ccblit(b, w, h)
        if v is not None:
            res.append(('cc_blit %dx%d (CPU cyc)' % (w, h), v[0], 'blitter pending %d CPU cyc at rts' % v[1]))
    res.append(('clear_page', m_page(b, 'clear'), ''))
    res.append(('copy_page', m_page(b, 'copy'), ''))
    return res


def main():
    res = measure()
    lines = ['%-40s %10.1f  %s' % r for r in res]
    txt = '\n'.join(lines)
    if '--cmp' in sys.argv:
        name = sys.argv[sys.argv.index('--cmp') + 1]
        old = {}
        for ln in open(os.path.join(PROJ, 'out', 'measure_%s.txt' % name)):
            k = ln[:40].strip()
            try:
                old[k] = float(ln[40:52])
            except ValueError:
                pass
        print('%-40s %10s %10s %8s' % ('case', name, 'now', 'saved'))
        for n, c, note in res:
            o = old.get(n)
            if o:
                print('%-40s %10.1f %10.1f %7.1f%%  %s' % (n, o, c, 100.0 * (o - c) / o, note))
            else:
                print('%-40s %10s %10.1f  %s' % (n, '-', c, note))
    else:
        print(txt)
    if '--save' in sys.argv:
        name = sys.argv[sys.argv.index('--save') + 1]
        open(os.path.join(PROJ, 'out', 'measure_%s.txt' % name), 'w').write(txt + '\n')


if __name__ == '__main__':
    main()
