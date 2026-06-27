#!/usr/bin/env python3
"""gen_polylut.py - poly_byte MEMAC-B bank/window lookup tables (src/aw_polygon.asm).

poly_byte derives the VRAM bank + window-high byte from poff+1 (the offset's high
byte). The arithmetic form is 6x lsr + add + or; these 256-byte LUTs replace it
with two indexed reads on a cache miss. Values are bit-identical to the calc:
  bank[x]   = ((x >> 6) + POLY_BANK0) | $80     (POLY_BANK0 = $14)
  window[x] = (x & $3F) | >DATAW                (>DATAW = $40, the $4000 window)

Output: out/polylut.bin = bank(256) + window(256) = 512 bytes.
"""
import os
OUT = os.path.join(os.path.dirname(__file__), '..', 'out')
POLY_BANK0 = 0x14
DATAW_HI = 0x40                 # >$4000

bank = bytes(((x >> 6) + POLY_BANK0) | 0x80 for x in range(256))
win = bytes((x & 0x3F) | DATAW_HI for x in range(256))
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, 'polylut.bin'), 'wb').write(bank + win)
print(f'polylut.bin: {len(bank + win)} bytes (bank 256 + window 256). '
      f'bank[0]={bank[0]:#x} bank[$40]={bank[0x40]:#x} bank[$C0]={bank[0xC0]:#x} '
      f'win[$25]={win[0x25]:#x}')
