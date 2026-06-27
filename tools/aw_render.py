#!/usr/bin/env python3
"""
aw_render.py - PC reference renderer for Another World cinematic polygons.

This is the "how it will look on Atari" oracle: it decodes the AW polygon
format exactly like the original VM and fills the shapes the same way the
6502 + VBXE blitter will (flat colour, horizontal spans), then writes a PNG
through the converted VBXE palette. Use it to validate the polygon decode and
the raster math before/while porting to assembly.

Usage:
    python aw_render.py [offset] [x] [y] [zoom] [palette]
        offset  word offset into the polygon resource (default 0)
        x,y     centre point         (default 160,100)
        zoom    64 = 1:1             (default 64)
        palette AW palette index 0-31 (default 1)
"""
import os, sys
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "out")
W, H, ZBASE = 320, 200, 64


def load_palette(idx):
    p = open(os.path.join(OUT, "intro_pal.bin"), "rb").read()
    base = idx * 16 * 3
    pal = []
    for i in range(16):
        r, g, b = p[base+i*3], p[base+i*3+1], p[base+i*3+2]
        pal += [r << 1, g << 1, b << 1]      # 7-bit -> 8-bit for the PNG
    pal += [0] * (256*3 - len(pal))
    return pal


class Poly:
    def __init__(self, data):
        self.d = data

    def draw(self, draw, off, x, y, zoom, color):
        d = self.d
        i = d[off]; off += 1
        if i >= 0xC0:                              # single filled polygon
            col = (i & 0x3F) if (color & 0x80) else color
            self._fill(draw, off, col, zoom, x, y)
        else:
            i &= 0x3F
            if i == 2:                             # hierarchy of sub-polygons
                self._hier(draw, off, zoom, x, y, color)
            # i == 1 (single point) is unused by the intro

    def _fill(self, draw, off, color, zoom, ptx, pty):
        d = self.d
        bbw = d[off] * zoom // ZBASE; off += 1
        bbh = d[off] * zoom // ZBASE; off += 1
        n = d[off]; off += 1
        x0 = ptx - bbw // 2
        y0 = pty - bbh // 2
        pts = []
        for _ in range(n):
            px = x0 + d[off] * zoom // ZBASE; off += 1
            py = y0 + d[off] * zoom // ZBASE; off += 1
            pts.append((px, py))
        if n >= 3:
            draw.polygon(pts, fill=color)          # flat-shaded fill
        elif n == 2:
            draw.line(pts, fill=color)

    def _hier(self, draw, off, zoom, ptx, pty, color):
        d = self.d
        bx = ptx - d[off] * zoom // ZBASE; off += 1
        by = pty - d[off] * zoom // ZBASE; off += 1
        childs = d[off]; off += 1
        for _ in range(childs + 1):
            word = (d[off] << 8) | d[off+1]; off += 2
            cx = bx + d[off] * zoom // ZBASE; off += 1
            cy = by + d[off] * zoom // ZBASE; off += 1
            ccol = 0xFF
            if word & 0x8000:
                ccol = d[off] & 0x7F; off += 2
            self.draw(draw, (word & 0x7FFF) * 2, cx, cy, zoom, ccol)


def main():
    a = sys.argv[1:]
    off  = int(a[0], 0) if len(a) > 0 else 0
    x    = int(a[1], 0) if len(a) > 1 else 160
    y    = int(a[2], 0) if len(a) > 2 else 100
    zoom = int(a[3], 0) if len(a) > 3 else 64
    pidx = int(a[4], 0) if len(a) > 4 else 1

    data = open(os.path.join(OUT, "16.bin"), "rb").read()
    img = Image.new("P", (W, H), 0)
    img.putpalette(load_palette(pidx))
    Poly(data).draw(ImageDraw.Draw(img), off, x, y, zoom, 0xFF)

    path = os.path.join(OUT, "preview.png")
    img.convert("RGB").save(path)
    print(f"rendered offset={off:#x} at ({x},{y}) zoom={zoom} pal={pidx} "
          f"-> {os.path.normpath(path)}")


if __name__ == "__main__":
    main()
