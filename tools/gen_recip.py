#!/usr/bin/env python3
"""
gen_recip.py - reciprocal table for the polygon edge slope.

slope = (|dx| << 16) / dy.  Since |dx| < 256 across the whole intro (measured),
the 6502 can replace the 32-iteration 32/16 divide with an 8x16 multiply:

    slope ~= |dx| * recip[dy]      where recip[dy] = round(65536 / dy)

dy == 1 is handled in code (slope = |dx| << 16); recip[1] would be 65536 which
does not fit 16 bits. The approximation drifts <0.25 px over a 200-tall edge
(~20 differing px/frame vs the exact divide -- imperceptible).

Output: out/recip.bin = 256 low bytes followed by 256 high bytes (recip[0..255]).
"""
import os
OUT = os.path.join(os.path.dirname(__file__), '..', 'out')

recip = [0] * 256
for dy in range(2, 256):
    recip[dy] = round(65536 / dy)          # <= 32768, fits 16 bits
# recip[0], recip[1] unused (dy>=1; dy==1 special-cased in asm)

lo = bytes(r & 0xFF for r in recip)
hi = bytes((r >> 8) & 0xFF for r in recip)
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, 'recip.bin'), 'wb').write(lo + hi)
print(f'recip.bin: 512 bytes (256 lo + 256 hi). '
      f'recip[2]={recip[2]} recip[3]={recip[3]} recip[255]={recip[255]}')
