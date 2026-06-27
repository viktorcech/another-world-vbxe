#!/usr/bin/env python3
"""gen_fmul.py - square tables for the fmulu 8x8->16 multiply (src/aw_polygon.asm).

fmulu (Fox/Tqa):  a*b = sq1[a+b] - sq2[(a^0xFF)+b]
  sq1[i] = floor(i*i / 4)        i = 0..510
  sq2[j] = floor((255-j)**2 / 4) j = 0..510   (so sq2[255-a+b] = floor((a-b)**2/4))
Both factors enter as the self-modified low byte of a page-aligned table base
(a / a^0xFF) plus X (=b), so each table is 512 bytes (a+b reaches 510).

Output: out/fmul.bin = sq1l(512) + sq1h(512) + sq2l(512) + sq2h(512) = 2048 bytes.
"""
import os
OUT = os.path.join(os.path.dirname(__file__), '..', 'out')

sq1 = [(i * i) // 4 for i in range(511)]
sq2 = [((255 - j) * (255 - j)) // 4 for j in range(511)]
assert max(sq1) < 65536 and max(sq2) < 65536


def lo(t): return bytes(t[i] & 0xFF for i in range(511)) + b'\x00'
def hi(t): return bytes((t[i] >> 8) & 0xFF for i in range(511)) + b'\x00'


blob = lo(sq1) + hi(sq1) + lo(sq2) + hi(sq2)
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, 'fmul.bin'), 'wb').write(blob)
print(f'fmul.bin: {len(blob)} bytes (sq1l,sq1h,sq2l,sq2h x512). '
      f'check 5*3={sq1[8]-sq2[255-5+3]} 12*7={sq1[19]-sq2[255-12+7]}')
