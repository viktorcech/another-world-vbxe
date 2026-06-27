#!/usr/bin/env python3
"""gen_qsquare.py - quarter-square multiply table for qsmul (src/aw_polygon.asm).

qs[n] = floor(n*n / 4), n = 0..510 (511 entries + 1 pad).  An unsigned 8x8->16
product is  a*b = qs[a+b] - qs[|a-b|]  (exact for a,b in 0..255).  The asm reads
qs[a+b] (a+b up to 510) via a page-aligned base pointer, so the table is laid out
as 512 low bytes followed by 512 high bytes.

Output: out/qsquare.bin = qs_lo(512) + qs_hi(512) = 1024 bytes.
"""
import os
OUT = os.path.join(os.path.dirname(__file__), '..', 'out')

qs = [(n * n) // 4 for n in range(511)]
assert max(qs) < 65536
lo = bytes(qs[n] & 0xFF for n in range(511)) + b'\x00'   # 512 bytes (pad to page)
hi = bytes((qs[n] >> 8) & 0xFF for n in range(511)) + b'\x00'
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, 'qsquare.bin'), 'wb').write(lo + hi)
print(f'qsquare.bin: {len(lo + hi)} bytes (qs_lo 512 + qs_hi 512). '
      f'qs[2]={qs[2]} qs[10]={qs[10]} qs[510]={qs[510]}')
