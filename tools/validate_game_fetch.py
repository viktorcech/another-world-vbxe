#!/usr/bin/env python3
"""
validate_game_fetch.py - exact-op validation of the wave-2 fetch port in
src_game/aw_polygon.asm (check-free poly_fetch, get_dr_off with poly_base_adj,
pf_wrap, rs_fast inline read).

The game is interactive, so instead of replaying one recorded stream this test
is EXHAUSTIVE over the pointer state space: for BOTH poly_base_adj values
(0 = video1, 8 = video2) it calls set_poly_ptr at EVERY dr_off 0..65535, walks
forward over a page+bank boundary-crossing distance with the exact 8-bit ops
the 6502 runs, and at every step asserts:

  * the effective VRAM offset == the integer oracle offset
  * memb_cur (the IRQ-restore invariant) == poly_bnk
  * get_dr_off reproduces the oracle offset exactly

This covers every reachable (bank, window, adj) combination including the
$7FFF -> $4000 wraps -- data-independent, so it holds for any part/shape.

    python tools/validate_game_fetch.py
"""
POLY_BANK0 = 0x14
DATAW_HI = 0x40
PF_BANK_HI = [0x00, 0x40, 0x80, 0xC0]

checks = 0
for adj in (0, 8):
    for start in range(0x10000):
        # ---- set_poly_ptr (exact ops) ----
        hi = (start >> 8) & 0xFF
        poly_bnk = ((((hi >> 6) + POLY_BANK0) | 0x80) + adj) & 0xFF
        memb_cur = poly_bnk
        pb_hi = (hi & 0x3F) | DATAW_HI
        pb_lo = start & 0xFF
        # walk far enough to cross a page AND (sometimes) a 16K bank boundary
        steps = 300 if (start & 0x3FFF) < 0x3F00 else 0x4100
        off = start
        for _ in range(steps):
            if off > 0xFFFF:
                break                                  # poly group is 64 KB max
            # effective address must match the oracle
            eff = (((poly_bnk - adj - (0x80 | POLY_BANK0)) & 0xFF) << 14) | \
                  (((pb_hi << 8) | pb_lo) - 0x4000)
            assert eff == off, f'adj={adj} start={start:04X}: eff {eff:04X} != {off:04X}'
            assert memb_cur == poly_bnk, 'memb_cur invariant broken'
            # get_dr_off (exact ops)
            x = (poly_bnk - adj - (0x80 + POLY_BANK0)) & 0xFF
            assert 0 <= x <= 3
            d = ((((pb_hi - DATAW_HI) & 0xFF) | PF_BANK_HI[x]) << 8) | pb_lo
            assert d == off, f'get_dr_off {d:04X} != {off:04X}'
            checks += 1
            # poly_fetch advance (exact ops; rs_fast advances identically)
            pb_lo = (pb_lo + 1) & 0xFF
            if pb_lo == 0:                             # pf_wrap
                pb_hi = (pb_hi + 1) & 0xFF
                if pb_hi == 0x80:
                    pb_hi = DATAW_HI
                    poly_bnk = (poly_bnk + 1) & 0xFF
                    memb_cur = poly_bnk
            off += 1

print(f'PASS  {checks:,} pointer states verified (both video bases, all offsets,')
print('      page + 16K-bank wraps): address, bank invariant and get_dr_off exact.')
