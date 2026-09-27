#!/usr/bin/env python3
"""perf_gr7.py - what would a GRAPHICS 7 (ANTIC D) build cost on a stock 6502?

Counts the REAL raster work of the game (spans + their length + whole-page ops)
by instrumenting the project's own oracle rasteriser, then prices it in 6502
cycles for a 2 bit/pixel CPU span writer and compares against the frame budget.
"""
import os, sys, collections, statistics
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import aw_sim, game_sim

# ---- instrumentation --------------------------------------------------------
STAT = {'spans': 0, 'px320': 0, 'rows': 0}
_orig_span = aw_sim._span
def _span(page, page0, row, xa, xb, color):
    y = row // aw_sim.W
    if y % 2 == 0:                       # the stock-6502 path draws even scanlines only
        STAT['spans'] += 1
        STAT['px320'] += xb - xa + 1
    return _orig_span(page, page0, row, xa, xb, color)
aw_sim._span = _span
aw_sim.fill_poly_int.__globals__['_span'] = _span

PAGEOPS = {'fill': 0, 'copy': 0}
def _wrap(fn, key):
    def w(self):
        PAGEOPS[key] += 1
        return fn(self)
    return w
for cls in (aw_sim.VM, game_sim.GameVM):          # GameVM copies OPS at import time
    cls.OPS = list(cls.OPS)
    cls.OPS[0x0E] = _wrap(cls.OPS[0x0E], 'fill')
    cls.OPS[0x0F] = _wrap(cls.OPS[0x0F], 'copy')

# ---- 6502 cost model (GR.7: 160x96, 2 bpp, 40 bytes/row) --------------------
# per span : address math + mask setup + jump-table dispatch into an unrolled
#            "sta $xxxx,y" chain.  Middle bytes cost 5 cyc each (4 pixels).
SPAN_FIXED = 55
CYC_PER_BYTE = 5.5
PAGE_BYTES = 160 * 96 // 4               # 3840 B per GR.7 page
FILL_CYC = PAGE_BYTES * 5.0              # sta abs,y unrolled
COPY_CYC = PAGE_BYTES * 9.5              # lda abs,y / sta abs,y unrolled

# PAL frame budget: 312 lines x 114 cyc, minus ANTIC playfield DMA.
FRAME_PAL = 312 * 114
DMA_D = 96 * 40 + 312 * 9                # mode D fetches 40 B per 2-line row + refresh
DMA_E = 192 * 40 + 312 * 9               # mode E fetches every line
BUDGET_D = FRAME_PAL - DMA_D
BUDGET_E = FRAME_PAL - DMA_E

def run(name, part, n):
    for k in STAT: STAT[k] = 0
    for k in PAGEOPS: PAGEOPS[k] = 0
    if part is None:
        frames, _ = aw_sim.render_intro(n, 'int')
    else:
        frames = game_sim.GameVM(part).run(n)
    nf = max(1, len(frames))
    spans, px = STAT['spans'], STAT['px320']
    bytes_written = px / 2 / 4                        # 320-space px -> 160 px -> 2bpp bytes
    raster = spans * SPAN_FIXED + bytes_written * CYC_PER_BYTE
    pageops = PAGEOPS['fill'] * FILL_CYC + PAGEOPS['copy'] * COPY_CYC
    tot = (raster + pageops) / nf
    print(f"{name:22} {nf:4} snimok | spanov/snimka {spans/nf:7.1f} | px/snimka {px/nf:8.0f} "
          f"| fill {PAGEOPS['fill']/nf:4.2f} copy {PAGEOPS['copy']/nf:4.2f} "
          f"| raster {raster/nf:8.0f} cyc | page-ops {pageops/nf:7.0f} cyc "
          f"| SPOLU {tot:8.0f} cyc = {tot/BUDGET_D:5.2f} snimky ({50/max(1,tot/BUDGET_D):4.1f} fps)")
    return tot

print(f"PAL ramec: {FRAME_PAL} cyklov;  GR.7 (ANTIC D) DMA {DMA_D} -> rozpocet {BUDGET_D} cyc/snimka")
print(f"                              GR.15 (ANTIC E) DMA {DMA_E} -> rozpocet {BUDGET_E} cyc/snimka")
print(f"GR.7 stranka = {PAGE_BYTES} B; fill {FILL_CYC:.0f} cyc, copy {COPY_CYC:.0f} cyc\n")
tots = []
tots.append(run('intro', None, 1200))
for p, nm in ((16002, 'water'), (16003, 'jail'), (16004, 'cite'),
              (16005, 'arene'), (16006, 'luxe'), (16007, 'final')):
    tots.append(run(f'{p} {nm}', p, 260))
print(f"\npriemer: {statistics.mean(tots):.0f} cyc/snimka -> {50/(statistics.mean(tots)/BUDGET_D):.1f} fps (PAL)")
