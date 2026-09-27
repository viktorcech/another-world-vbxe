#!/usr/bin/env python3
"""perf_dirty.py - how much of the page really has to be restored each frame?

A full GR.7 page copy costs ~36 500 cyc = one whole PAL frame, so a straight
port of op_copypage is a non-starter.  This measures the DIRTY AREA instead:
per frame, the per-row x-extent actually touched by the rasteriser, i.e. what a
dirty-row restore would have to copy back from the background page.
"""
import os, sys, statistics
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import aw_sim, game_sim

ROWS = 96                                   # GR.7 rows
DIRTY = {}                                  # row -> [xmin, xmax] in 160-space
FRAMES = []

_orig = aw_sim._span
def _span(page, page0, row, xa, xb, color):
    y = (row // aw_sim.W) * ROWS // aw_sim.H
    a, b = xa // 2, xb // 2
    d = DIRTY.get(y)
    if d is None:
        DIRTY[y] = [a, b]
    else:
        if a < d[0]: d[0] = a
        if b > d[1]: d[1] = b
    return _orig(page, page0, row, xa, xb, color)
aw_sim._span = _span
aw_sim.fill_poly_int.__globals__['_span'] = _span

_oud = aw_sim.VM.op_updatedisplay
def op_updatedisplay(self):
    rows = len(DIRTY)
    bts = sum((d[1] // 4) - (d[0] // 4) + 1 for d in DIRTY.values())
    FRAMES.append((rows, bts))
    DIRTY.clear()
    return _oud(self)
for cls in (aw_sim.VM, game_sim.GameVM):
    cls.OPS = list(cls.OPS)
    cls.OPS[0x10] = op_updatedisplay

PAGE_BYTES = 160 * ROWS // 4
COPY_PER_BYTE = 9.5                         # unrolled lda abs,y / sta abs,y
FRAME_CYC = 35568
BUDGET = FRAME_CYC - (96 * 40 + 312 * 9)    # GR.7 (ANTIC D) CPU budget

def run(name, part, n):
    FRAMES.clear(); DIRTY.clear()
    if part is None:
        aw_sim.render_intro(n, 'int')
    else:
        game_sim.GameVM(part).run(n)
    if not FRAMES:
        print(f'{name}: no frames'); return
    rows = [f[0] for f in FRAMES]; bts = [f[1] for f in FRAMES]
    med, p90 = statistics.median(bts), sorted(bts)[int(len(bts) * .9)]
    print(f"{name:16} snimok {len(FRAMES):4} | dirty riadkov med {statistics.median(rows):5.0f}/{ROWS}"
          f" | dirty bajtov med {med:5.0f} p90 {p90:5.0f} max {max(bts):5.0f} ({max(bts)*100//PAGE_BYTES:3}% strany)"
          f" | restore med {med*COPY_PER_BYTE:7.0f} cyc p90 {p90*COPY_PER_BYTE:7.0f} cyc"
          f" = {p90*COPY_PER_BYTE/BUDGET*100:4.0f}% ramca")

print(f"cela GR.7 stranka = {PAGE_BYTES} B -> plna kopia {PAGE_BYTES*COPY_PER_BYTE:.0f} cyc "
      f"({PAGE_BYTES*COPY_PER_BYTE/BUDGET*100:.0f} % ramca)\n")
run('intro', None, 1200)
for p, nm in ((16002,'water'),(16003,'jail'),(16004,'cite'),(16005,'arene'),(16006,'luxe'),(16007,'final')):
    run(f'{p} {nm}', p, 260)
