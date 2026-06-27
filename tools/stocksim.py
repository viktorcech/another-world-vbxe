#!/usr/bin/env python3
"""
stocksim.py - simulate what a STOCK Atari draws for our scenes.

NO polygons. Renders an ANTIC mode 4 (multicolour character mode) background
from a charset + tilemap, then overlays PMG players (player + enemy) exactly
like the GTIA would, and writes a PNG so we can SEE the result.

Grid: 160 x 192 "fat pixels" (ANTIC 4 = 40 cells * 4 px wide, 24 rows * 8 tall).
A PMG player bit at normal size = 1 fat pixel, so sprites overlay 1:1.
"""
import os
from PIL import Image

OUT = os.path.join(os.path.dirname(__file__), "..", "out")
os.makedirs(OUT, exist_ok=True)

CW, CH = 40, 24          # cells
GW, GH = 160, 192        # fat-pixel grid

# ---- Atari-ish palette (RGB) ------------------------------------------------
DARK   = (12, 12, 28)    # COLBK : cave / void
BLUE   = (44, 70, 170)   # COLPF0: water body
CYAN   = (170, 210, 235) # COLPF1: water highlight / surface
ROCK   = (120, 72, 34)   # COLPF2: rock / wall / ground
GREY   = (96, 96, 104)   # COLPF3: rock shade (inverse-bit cells)
SKIN   = (228, 180, 150) # player
ENEMY  = (70, 190, 80)   # creature

# ---- charset : each glyph = 8 bytes, each byte = 4 two-bit pixels -----------
def g(*rows):
    return list(rows)

def row(a, b, c, d):       # 4 two-bit pixels, MSB-first
    return (a << 6) | (b << 4) | (c << 2) | d

EMPTY  = g(*[row(0,0,0,0)]*8)
WALL   = g(*[row(2,2,2,2)]*8)                              # solid rock (PF2)
FLOOR  = g(*[row(2,2,2,2)]*8)
WATER  = g(row(1,1,1,1), row(1,1,2,1), row(1,1,1,1), row(2,1,1,1),
           row(1,1,1,1), row(1,2,1,1), row(1,1,1,1), row(1,1,1,2))
SURF   = g(row(2,2,2,2), row(2,1,2,1), row(1,1,1,1), row(1,1,1,1),
           row(1,1,1,1), row(1,1,1,1), row(1,1,1,1), row(1,1,1,1))

CHARSET = {0: EMPTY, 1: WATER, 2: WALL, 3: SURF, 4: FLOOR}

# ---- PMG sprites : list of 8-bit rows (MSB = leftmost) ----------------------
def b(s):
    return int(s, 2)

PLAYER = [b(x) for x in (
    "00111100", "00111100", "00011000", "00111100",
    "01111110", "11011011", "01111110", "00111100",
    "00111100", "00111100", "00100100", "00100100",
    "00100100", "01100110", "01100110", "11000011",
)]

SQUID = [b(x) for x in (
    "00111100", "01111110", "11111111", "11111111",
    "11111111", "01111110", "00100100", "01000010", "10000001",
)]

# ---------------------------------------------------------------------------
def render(tilemap, sprites):
    grid = [[DARK]*GW for _ in range(GH)]
    for cy in range(CH):
        for cx in range(CW):
            code = tilemap[cy][cx]
            glyph = CHARSET[code & 0x7F]
            pf2 = GREY if (code & 0x80) else ROCK
            pal = [DARK, BLUE, CYAN, pf2]
            for sl in range(8):
                rb = glyph[sl]
                for px in range(4):
                    val = (rb >> (6 - px*2)) & 3
                    if val:
                        grid[cy*8+sl][cx*4+px] = pal[val]
    for shape, x, y, color, size in sprites:
        bw = 1 << size
        for r, byte in enumerate(shape):
            yy = y + r
            if not (0 <= yy < GH):
                continue
            for bit in range(8):
                if byte & (0x80 >> bit):
                    for k in range(bw):
                        xx = x + bit*bw + k
                        if 0 <= xx < GW:
                            grid[yy][xx] = color
    img = Image.new("RGB", (GW, GH))
    px = img.load()
    for yy in range(GH):
        for xx in range(GW):
            px[xx, yy] = grid[yy][xx]
    return img.resize((GW*4, GH*3), Image.NEAREST)


def water_scene():
    tm = [[0]*CW for _ in range(CH)]
    for cy in range(CH):
        for cx in range(CW):
            if cx < 4 or cx >= CW-4:
                tm[cy][cx] = 2
            elif cy == 6:
                tm[cy][cx] = 3
            elif cy > 6:
                tm[cy][cx] = 1
            if cy >= CH-1:
                tm[cy][cx] = 4
    sprites = [(PLAYER, 78, 80, SKIN, 0), (SQUID, 104, 150, ENEMY, 1)]
    return render(tm, sprites)


def land_scene():
    tm = [[0]*CW for _ in range(CH)]
    for cy in range(CH):
        for cx in range(CW):
            if cy >= 19:
                tm[cy][cx] = 2
    for cy in range(19, CH):
        for cx in range(0, 10):
            tm[cy][cx] = 1
    sprites = [(PLAYER, 40, 140, SKIN, 0), (SQUID, 110, 138, ENEMY, 1)]
    return render(tm, sprites)


if __name__ == "__main__":
    water_scene().save(os.path.join(OUT, "scene_water.png"))
    land_scene().save(os.path.join(OUT, "scene_land.png"))
    print("wrote out/scene_water.png and out/scene_land.png")
