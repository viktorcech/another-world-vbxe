"""bench_skillpass.py - cycles per drawn scanline / per shape on the hot paths, old vs new build.
Runs poly_draw on hand-built shapes through the cycle-counting 6502 core (same harness as
verify_skillpass.py). Spans = fired BCBs; cycles exclude nothing (decode + raster + fill_span).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import _cpu6502 as cpu6502, _verify_skillpass as V
import _smcvars as SV                                             # noqa: E402


def shape(col, bbw, bbh, pts):
    b = bytearray([0xC0 | col, bbw, bbh, len(pts)])
    for x, y in pts:
        b += bytes([x, y])
    return bytes(b)


CASES = [
    # name, stream, dr_x, dr_y, zoom, half
    ('big trapezoid, on-screen, full detail', shape(5, 200, 150, [(180, 0), (200, 150), (0, 150), (20, 0)]), 160, 100, 64, 0),
    ('big trapezoid, on-screen, half detail', shape(5, 200, 150, [(180, 0), (200, 150), (0, 150), (20, 0)]), 160, 100, 64, 1),
    ('tall quad, x-clipped (yok path), full', shape(7, 120, 180, [(120, 0), (120, 180), (0, 180), (0, 0)]), 30, 100, 64, 0),
    ('tall quad, y-clipped (full clip path), full', shape(7, 120, 180, [(120, 0), (120, 180), (0, 180), (0, 0)]), 160, 40, 64, 0),
    ('small sprite 8 verts, on-screen, half', shape(3, 24, 30, [(14, 0), (24, 12), (23, 20), (22, 30), (2, 30), (0, 12), (4, 2), (10, 0)]), 160, 100, 64, 1),
    ('small sprite 8 verts, zoom 128, half', shape(3, 24, 30, [(14, 0), (24, 12), (23, 20), (22, 30), (2, 30), (0, 12), (4, 2), (10, 0)]), 160, 100, 128, 1),
]


def run(b, stream, dx, dy, zoom, half):
    L = b.L
    cpu, mem, log = b.fresh()
    mem[0x4000:0x4000 + len(stream)] = stream
    if 'hires' in L:
        SV.poke(mem, L, 'hires', 0)
    SV.poke(mem, L, 'poly_bcb_h', half)
    V.w16(mem, L['dr_zoom'], zoom); V.w16(mem, L['dr_x'], dx); V.w16(mem, L['dr_y'], dy)
    mem[L['dr_col']] = 0xFF; V.w16(mem, L['dr_off'], 0)
    for k in ('poly_base_adj', 'cc_baking', 'cc_flag'):
        if k in L:
            mem[L[k]] = 0
    mem[L['psp']] = 0
    mem[L['last_scol']] = 0xFF; mem[L['memb_cur']] = 0
    if zoom == 64:
        V.w16(mem, L['rs_smc'] + 1, L['rs_fast'])
    elif 'rs_z4' in L:
        V.w16(mem, L['rs_smc'] + 1, L['rs_z4']); b.call(cpu, L['rs_z4_set'])
    else:
        V.w16(mem, L['rs_smc'] + 1, L['rs_slow'])
    b.call(cpu, L['set_poly_ptr'])
    cpu.cyc = 0
    b.call(cpu, L['poly_draw'])
    return cpu.cyc, len(log)


if __name__ == '__main__':
    if '--intro' in sys.argv:
        print('== INTRO build =='); old = V.Build(*V.OLD_INTRO); new = V.Build(*V.NEW_INTRO)
    else:
        print('== GAME build =='); old = V.Build(*V.OLD); new = V.Build(*V.NEW)
    print('%-46s %7s %9s %9s %8s %8s %6s' % ('case', 'spans', 'old cyc', 'new cyc', 'old/sl', 'new/sl', 'save'))
    for name, st, dx, dy, z, h in CASES:
        co, so = run(old, st, dx, dy, z, h); cn, sn = run(new, st, dx, dy, z, h)
        assert so == sn
        print('%-46s %7d %9d %9d %8.1f %8.1f %5.1f%%' % (name, so, co, cn, co / so, cn / sn, 100.0 * (co - cn) / co))
