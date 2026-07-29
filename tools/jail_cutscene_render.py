#!/usr/bin/env python3
"""jail_cutscene_render.py - render the JAIL window-cutscene shapes individually
(NO VM) so we can see which one is the missing side-column/frame. Each shape is
decoded straight from its poly bank and drawn centred on a blank page; output is a
labelled contact sheet  out/screens/jail_cutscene_shapes.png  (PC 320 oracle).

Run from tools/:  python jail_cutscene_render.py
"""
import os
import aw_sim, game_sim

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
DATA = os.path.join(PROJ, 'out', 'jaildata')
OUT = os.path.join(PROJ, 'out', 'screens', 'jail_cutscene_shapes.png')
W, H = aw_sim.W, aw_sim.H

poly1 = open(os.path.join(DATA, 'poly1.bin'), 'rb').read()
poly2 = open(os.path.join(DATA, 'poly2.bin'), 'rb').read()
pd1 = aw_sim.PolyData(poly1)
pd2 = aw_sim.PolyData(poly2)

# palette from the part (cutscene screen 1 uses setpal 9)
pals = game_sim.GameVM(16003, 'int').pals
PAL = pals[9]

# (offset, bank) — foreground = video1, panorama = video2
FG = [896, 2124, 2260, 2812, 2854, 2926, 2998, 3338, 19520]
BG = [17548, 17704, 17856, 18000, 18132, 18260, 18396, 18524, 18648,
      18772, 18948, 19148, 19396, 22468, 22648, 22852, 23048, 23212, 23372]


def render_shape(off, pd):
    page = bytearray(aw_sim.SIZE)         # blank (colour 0)
    pd.page0 = page
    pd.draw(page, off, 160, 100, 64, 0xFF)
    return page


def _png(path, w, h, rgb):
    import struct, zlib
    def chunk(t, d): c = t + d; return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)))
        rows = b''.join(b'\x00' + rgb[y*w*3:(y+1)*w*3] for y in range(h))
        f.write(chunk(b'IDAT', zlib.compress(rows, 6)))
        f.write(chunk(b'IEND', b''))


def main():
    items = [(o, pd1, 'v1') for o in FG] + [(o, pd2, 'v2') for o in BG]
    cols = 5
    rows = (len(items) + cols - 1) // cols
    # thumbnail: half-size 160x100
    tw, th = W // 2, H // 2
    pad = 4
    cw = cols * (tw + pad) + pad
    ch = rows * (th + pad) + pad
    sheet = bytearray(b'\x20\x20\x30' * (cw * ch))
    for idx, (off, pd, tag) in enumerate(items):
        page = render_shape(off, pd)
        rgb = aw_sim.frame_to_rgb(page, PAL)          # 320x200 rgb
        r, c = divmod(idx, cols)
        ox = pad + c * (tw + pad); oy = pad + r * (th + pad)
        for y in range(th):                            # downscale 2x by sampling
            sy = y * 2
            for x in range(tw):
                sx = x * 2
                s = (sy * W + sx) * 3
                d = ((oy + y) * cw + (ox + x)) * 3
                sheet[d:d+3] = rgb[s:s+3]
    _png(OUT, cw, ch, bytes(sheet))
    print("order (left->right, top->bottom):")
    for idx, (off, pd, tag) in enumerate(items):
        end = '\n' if idx % cols == cols - 1 else '   '
        print(f"[{idx:2}] {tag} off={off}", end=end)
    print(f"\n\n-> {os.path.normpath(OUT)}")


if __name__ == '__main__':
    main()
