#!/usr/bin/env python3
"""
validate_fetch.py - exact-op validation of the 2026-06-10 fetch rework in
src/aw_polygon.asm (check-free poly_fetch, get_dr_off derive-at-save, running
pl_byte pointer, rs_fast inline read).

Replays the WHOLE intro (out/intro_playlist.bin + intro_poly.bin) with TWO
pointer models side by side:

  oracle : a plain integer offset (what the bytes SHOULD be), mirroring the
           exact read ORDER of the asm decoder (poly_draw/do_fill/do_hier,
           including the ccol skip byte and the hier save/restore re-syncs).
  m6502  : the new asm's pointer state (poly_bnk, pb_ptr, pl_bnk, pl_w*,
           memb_cur) advanced with the SAME 8-bit operations the 6502 runs
           (set_poly_ptr LUTs, inc/wrap at $7FFF->$4000+bank, get_dr_off).

Asserts, for every single byte of the run:
  * the m6502 effective VRAM address == oracle address  (poly + playlist)
  * the MEMAC-B register (memb_cur) == the bank the read needs
  * get_dr_off's derived dr_off == the oracle offset at every do_hier save

Per the project methodology the model mirrors the asm ops byte-for-byte (the
"replicate the exact 6502 ops" lesson) -- a pass means the 6502 pointer
arithmetic is right; the rendering itself is untouched by the rework.

    python tools/validate_fetch.py
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'out')

POLY_BANK0 = 0x14            # $050000 >> 14
PLAY_BANK0 = 0x18            # $060000 >> 14
DATAW_HI = 0x40              # window $4000
PF_BANK_HI = [0x00, 0x40, 0x80, 0xC0]      # pf_bank_hi LUT

poly = open(os.path.join(OUT, 'intro_poly.bin'), 'rb').read()
pl = open(os.path.join(OUT, 'intro_playlist.bin'), 'rb').read()

# ---- the 6502 state ----------------------------------------------------------
st = {
    'poly_bnk': 0, 'pb_lo': 0, 'pb_hi': 0,        # poly stream ptr
    'pl_bnk': 0x80 | PLAY_BANK0, 'pl_lo': 0x00, 'pl_hi': DATAW_HI,  # playlist ptr
    'memb_cur': 0x00,                              # MEMAC-B register mirror
}
checks = {'poly_rd': 0, 'pl_rd': 0, 'save': 0, 'setptr': 0}


def poly_eff():
    """effective poly-data offset of (poly_bnk, pb_ptr)"""
    return (((st['poly_bnk'] & 0x7F) - POLY_BANK0) << 14) | \
           (((st['pb_hi'] << 8) | st['pb_lo']) - 0x4000)


def set_poly_ptr(dr_off):
    """exact mirror of asm set_poly_ptr: LUTs on dr_off hi + unconditional bank"""
    hi = (dr_off >> 8) & 0xFF
    st['poly_bnk'] = ((hi >> 6) + POLY_BANK0) | 0x80   # poly_bank_lut[hi]
    st['memb_cur'] = st['poly_bnk']                    # sta memb_cur + sta MEMAC_B
    st['pb_hi'] = (hi & 0x3F) | DATAW_HI               # poly_win_lut[hi]
    st['pb_lo'] = dr_off & 0xFF
    checks['setptr'] += 1


def poly_fetch(oracle_off):
    """exact mirror of asm poly_fetch/rs_fast + pf_wrap (identical pointer ops)"""
    assert st['memb_cur'] == st['poly_bnk'], \
        f"poly read with MEMAC-B={st['memb_cur']:02X} != poly_bnk={st['poly_bnk']:02X}"
    eff = poly_eff()
    assert eff == oracle_off, f"poly addr {eff} != oracle {oracle_off}"
    val = poly[oracle_off]
    st['pb_lo'] = (st['pb_lo'] + 1) & 0xFF             # inc pb_ptr
    if st['pb_lo'] == 0:                               # beq pf_wrap
        st['pb_hi'] = (st['pb_hi'] + 1) & 0xFF
        if st['pb_hi'] == 0x80:                        # past $7FFF
            st['pb_hi'] = DATAW_HI
            st['poly_bnk'] = (st['poly_bnk'] + 1) & 0xFF
            st['memb_cur'] = st['poly_bnk']
    checks['poly_rd'] += 1
    return val


def get_dr_off():
    """exact mirror of asm get_dr_off"""
    x = (st['poly_bnk'] - (0x80 + POLY_BANK0)) & 0xFF
    assert 0 <= x <= 3, f"bank delta {x} out of LUT range"
    hi = ((st['pb_hi'] - DATAW_HI) & 0xFF) | PF_BANK_HI[x]
    return (hi << 8) | st['pb_lo']


def pl_byte(oracle_off):
    """exact mirror of the new running-pointer pl_byte"""
    if st['pl_bnk'] != st['memb_cur']:                 # cmp memb_cur / bank switch
        st['memb_cur'] = st['pl_bnk']
    eff = (((st['pl_bnk'] & 0x7F) - PLAY_BANK0) << 14) | \
          (((st['pl_hi'] << 8) | st['pl_lo']) - 0x4000)
    assert eff == oracle_off, f"pl addr {eff} != oracle {oracle_off}"
    val = pl[oracle_off]
    st['pl_lo'] = (st['pl_lo'] + 1) & 0xFF
    if st['pl_lo'] == 0:
        st['pl_hi'] = (st['pl_hi'] + 1) & 0xFF
        if st['pl_hi'] == 0x80:
            st['pl_hi'] = DATAW_HI
            st['pl_bnk'] = (st['pl_bnk'] + 1) & 0xFF
            st['memb_cur'] = st['pl_bnk']
    checks['pl_rd'] += 1
    return val


# ---- poly decoder walk (read ORDER mirrors asm poly_draw/do_fill/do_hier) ----
sys.setrecursionlimit(100)


def draw(off):
    """off = oracle offset; m6502 ptr was just set via set_poly_ptr(off)"""
    b0 = poly_fetch(off); off += 1
    if b0 >= 0xC0:                                     # do_fill
        off = rd_n(off, 3)                             # bbw, bbh, nverts
        n = poly[off - 1]
        off = rd_n(off, 2 * n)                         # vertex pairs (read_scaled)
        return
    if (b0 & 0x3F) != 2:
        return                                         # ?ret (no more reads)
    off = rd_n(off, 3)                                 # bx, by, child count
    count = poly[off - 1]
    for _ in range(count + 1):
        whi = poly_fetch(off); off += 1                # word hi
        wlo = poly_fetch(off); off += 1                # word lo
        off = rd_n(off, 2)                             # cx, cy
        if whi & 0x80:
            off = rd_n(off, 2)                         # ccol + skip byte
        # --- save: get_dr_off must reproduce the oracle offset exactly ---
        d = get_dr_off()
        assert d == off, f"get_dr_off {d} != oracle {off}"
        checks['save'] += 1
        child = (((whi << 8) | wlo) & 0x7FFF) * 2
        set_poly_ptr(child)                            # child entry re-sync
        draw(child)
        set_poly_ptr(off)                              # restore re-sync
    return


def rd_n(off, n):
    for _ in range(n):
        poly_fetch(off); off += 1
    return off


# ---- playlist walk (read ORDER mirrors aw_replayer op handlers) --------------
p = 0
ops = 0
while p < len(pl):
    op = pl_byte(p); p += 1
    ops += 1
    if op == 0x00:
        break
    elif op == 0x01 or op == 0x02 or op == 0x08:       # setpal / selpage / sound
        pl_byte(p); p += 1
    elif op == 0x03 or op == 0x04 or op == 0x06:       # fill / copy / blit
        pl_byte(p); pl_byte(p + 1); p += 2
    elif op == 0x07:                                   # drawtext: 5 operand bytes
        for i in range(5):
            pl_byte(p + i)
        p += 5
    elif op == 0x05:                                   # drawpoly: 8 operands
        b = [pl_byte(p + i) for i in range(8)]
        p += 8
        dr_off = b[0] | (b[1] << 8)
        set_poly_ptr(dr_off)
        draw(dr_off)
    else:
        raise AssertionError(f'unknown opcode {op:02X} at {p-1}')

print(f"PASS  ops={ops}  poly reads={checks['poly_rd']:,}  pl reads={checks['pl_rd']:,}")
print(f"      hier saves(get_dr_off)={checks['save']:,}  set_poly_ptr={checks['setptr']:,}")
print('      every read hit the exact oracle address with the right MEMAC-B bank.')
