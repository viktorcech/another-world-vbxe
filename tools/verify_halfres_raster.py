#!/usr/bin/env python3
"""
verify_halfres_raster.py - prove the proposed half-res raster loop change is
byte-IDENTICAL to the current one, INCLUDING cross-segment edge continuity.
Fresh tool modelling the CURRENT asm's 32-bit edge accumulator exactly.

Current (aw_raster.asm, half-res poly_bcb_h=1): the row loop runs for EVERY
screen row of a segment; it draws a 2-tall span on even rows (rpar starts 0 at
the poly top) and skips the draw on odd rows -- but `add_steps` (the 32-bit edge
step) + loop overhead run on EVERY row. The integer edge (cr2:cr3 / cl2:cl3)
CONTINUES across segments (only the low word resets to the rounding bias each
segment); a zero-height segment still advances the edge once to track the vertex.

Proposed: iterate only the DRAWN rows (~half), step 2*slope each. This deletes
the skipped rows' add_steps + overhead (~40% of the rasterizer's per-scanline
CPU). To stay byte-identical we must reproduce BOTH:
  (1) the integer edge at every drawn row, and
  (2) the FINAL integer edge after the segment (next segment continues from it).
Special cases handled:
  - H == 0 : advance the edge ONCE by 1*slope (vertex tracking), no draw.
  - H odd  : ceil(H/2) iters of 2*slope overshoot by one slope -> subtract 1*slope
             at the end so the final edge == current's init + H*slope.

We sweep realistic slopes (dx/dy), all heights incl. 0/1/odd/even, both edge
biases, and several start x's, asserting BOTH the drawn-row edges and the final
continuity edge match.
"""

MASK32 = 0xFFFFFFFF

def model_current(init32, slope32, H):
    acc, drawn = init32 & MASK32, []
    for r in range(H):
        if (r & 1) == 0:                       # rpar even -> drawn 2-tall span
            drawn.append((acc >> 16) & 0xFFFF)
        acc = (acc + slope32) & MASK32         # add_steps every row
    if H == 0:                                 # zero-height: advance once (track vertex)
        acc = (acc + slope32) & MASK32
    return drawn, (acc >> 16) & 0xFFFF

def model_proposed(init32, slope32, H):
    acc, drawn = init32 & MASK32, []
    if H == 0:
        acc = (acc + slope32) & MASK32         # same 1*slope vertex track, no draw
        return drawn, (acc >> 16) & 0xFFFF
    step2 = (slope32 * 2) & MASK32             # doubled slope (4-byte shift-left at setup)
    r = 0
    while r < H:
        drawn.append((acc >> 16) & 0xFFFF)     # draw 2-tall, then step 2 rows
        acc = (acc + step2) & MASK32
        r += 2
    if H & 1:                                  # odd-height correction: back off one slope
        acc = (acc - slope32) & MASK32
    return drawn, (acc >> 16) & 0xFFFF

def main():
    bad = checked = 0
    for bias in (0x7FFF, 0x8000):
        for x0 in (0, 80, 159, -40, 200):
            for dy in range(0, 201):                 # incl. H==0
                hh = dy if dy > 0 else 1
                for dx in range(-319, 320, 5):
                    slope32 = ((dx << 16) // hh) & MASK32
                    init32 = ((x0 & 0xFFFF) << 16) | bias
                    checked += 1
                    if model_current(init32, slope32, dy) != model_proposed(init32, slope32, dy):
                        bad += 1
                        if bad <= 5:
                            dc, fc = model_current(init32, slope32, dy)
                            dp, fp = model_proposed(init32, slope32, dy)
                            print(f"  MISMATCH x0={x0} dy={dy} dx={dx}: "
                                  f"final cur={fc:#06x} new={fp:#06x} | drawn== {dc==dp}")
    if bad == 0:
        print(f"OK: drawn-row edges AND cross-segment final edge identical in all "
              f"{checked} cases (incl. H=0/odd/even) -> restructure is output-identical.")
    else:
        print(f"FAIL: {bad}/{checked} mismatches.")

if __name__ == "__main__":
    main()
