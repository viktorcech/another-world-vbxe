#!/usr/bin/env python3
"""make_mockup.py - render "how Another World would look in GRAPHICS 7" sheets.

Every frame is a real frame out of the project's own oracle (tools/aw_sim.py for the
intro, tools/game_sim.py for the game parts).  Usage:  python gfx7/make_mockup.py
"""
import os, sys, collections
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import aw_sim, game_sim
import gr7lib as G
from gr7lib import W, H

BANDS = 8                                    # DLI zones for the "GR.7 + DLI" variant

SCENES = [
    ('intro',  None,  1050, 'INTRO - mesto / auto'),
    ('water',  16002,   70, 'WATER - bazen (start hry)'),
    ('jail',   16003,  205, 'JAIL - vezenie'),
    ('arene',  16005,  210, 'ARENE - arena'),
    ('luxe',   16006,   60, 'LUXE - jaskyne'),
    ('final',  16007,  195, 'FINAL - finale'),
]


def run(part, nframes):
    if part is None:
        return aw_sim.render_intro(nframes, 'int')
    vm = game_sim.GameVM(part)
    fr = vm.run(nframes)
    return fr, vm.pals


def collect(part, nframes):
    """-> frames, pals, hist[pal] (16,), bandhist[pal] (BANDS,16)"""
    frames, pals = run(part, nframes)
    hist = collections.defaultdict(lambda: np.zeros(16, np.int64))
    bh = collections.defaultdict(lambda: np.zeros((BANDS, 16), np.int64))
    for page, pal, hold, draws, dl in frames:
        a = np.frombuffer(bytes(page), np.uint8).reshape(H, W)
        hist[pal] += np.bincount(a.ravel(), minlength=16)
        for b in range(BANDS):
            y0, y1 = b * H // BANDS, (b + 1) * H // BANDS
            bh[pal][b] += np.bincount(a[y0:y1].ravel(), minlength=16)
    return frames, pals, hist, bh


def font(size, bold=False):
    for f in (r'C:\Windows\Fonts\segoeuib.ttf' if bold else r'C:\Windows\Fonts\segoeui.ttf',
              r'C:\Windows\Fonts\arialbd.ttf' if bold else r'C:\Windows\Fonts\arial.ttf',
              r'C:\Windows\Fonts\consola.ttf'):
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            pass
    return ImageFont.load_default()


COLS = [
    ('ORIGINAL (PC)',      '320x200, 16 farieb'),
    ('VBXE - dnesny port', '160x200, 16 farieb (8bpp)'),
    ('GR.15 / ANTIC E',    '160x192, 4 farby'),
    ('GR.7 / ANTIC D',     '160x96, 4 farby'),
    ('GR.7 + DLI pasma',   '160x96, 4 farby / pasmo'),
    ('GR.10 / GTIA',       '80x192, 9 farieb'),
    ('GR.9 / GTIA',        '80x192, 16 odtienov'),
]


def build():
    tiles = []
    for name, part, fidx, label in SCENES:
        frames, pals, hist, bh = collect(part, max(300, fidx + 10))
        fidx = min(fidx, len(frames) - 1)
        page, pal, hold, draws, dl = frames[fidx]
        aw_pal = pals[pal]
        regs, lut = G.palette_lut(aw_pal, hist[pal], 4)
        bregs, bluts = G.palette_lut(aw_pal, hist[pal], 4, BANDS, bh[pal])
        # the DLI bands are defined on the 200-line source; the same split applies
        r9, l9 = G.palette_lut(aw_pal, hist[pal], 9)
        hue, lut9 = G.gr9_lut(aw_pal, hist[pal])
        row = [
            G.pad200(G.render_original(page, aw_pal)),
            G.pad200(G.render_vbxe_lr(page, aw_pal)),
            G.pad200(G.render_antic(page, regs, lut, 192)),
            G.pad200(G.render_antic(page, regs, lut, 96)),
            G.pad200(G.render_antic(page, regs, lut, 96, list(zip(bregs, bluts)))),
            G.pad200(G.render_gr10(page, r9, l9)),
            G.pad200(G.render_gr9(page, hue, lut9)),
        ]
        used = int((hist[pal] > 0).sum())
        tiles.append((label, f'AW paleta #{pal}, {used} farieb v scene', row))
        print(f'  {name} f{fidx}: pal={pal} used={used} regs={[hex(r) for r in regs]}')
    return tiles


def compose(tiles, path):
    TW, TH = 320, 200
    pad, gapx, gapy = 28, 14, 54
    left = 268
    top = 168
    f_title = font(32, True); f_col = font(18, True); f_sub = font(15)
    f_row = font(17, True); f_rows = font(13)
    ncol = len(COLS)
    Wpx = left + ncol * TW + (ncol - 1) * gapx + pad
    Hpx = top + len(tiles) * (TH + gapy) + pad
    img = Image.new('RGB', (Wpx, Hpx), (18, 18, 22))
    d = ImageDraw.Draw(img)
    d.text((pad, 24), 'Another World na Atari:  VBXE  vs.  GRAPHICS 7 / 15  vs.  GTIA rezimy', (240, 240, 245), f_title)
    for i, ln in enumerate([
            'Vsetky snimky su REALNE snimky hry z oracle simulatora projektu (tools/aw_sim.py + tools/game_sim.py) - rovnaky rasterizer, aky bezi na 6502.',
            'Farby: Altirra "Default NTSC (XL)" paleta vygenerovana z alt-src/Altirra/source/palettegenerator.cpp. Pomer pixelov je zachovany (160-bodovy pixel = 2:1).',
            'Mapovanie 16 -> 4 farby sa pocita PER AW PALETU z histogramu vsetkych snimok, ktore ju pouzivaju - presne tak, ako by sa tabulka zapiekla do buildu.']):
        d.text((pad, 66 + i * 21), ln, (150, 150, 160), f_sub)
    for c, (t, s) in enumerate(COLS):
        x = left + c * (TW + gapx)
        col = (255, 210, 120) if 2 <= c <= 4 else (140, 220, 200) if c >= 5 else (170, 200, 255)
        d.text((x, top - 44), t, col, f_col)
        d.text((x, top - 23), s, (140, 140, 150), f_sub)
    for r, (label, sub, row) in enumerate(tiles):
        y = top + r * (TH + gapy)
        d.text((pad, y + 6), label, (235, 235, 240), f_row)
        d.text((pad, y + 28), sub, (140, 140, 150), f_rows)
        for c, tile in enumerate(row):
            x = left + c * (TW + gapx)
            img.paste(Image.fromarray(tile), (x, y))
            d.rectangle([x - 1, y - 1, x + TW, y + TH], outline=(60, 60, 70))
    img.save(path)
    print('->', path, img.size)


def gallery(tiles, path, col=4, scale=2):
    """Big view of one variant (column `col`) across all scenes."""
    TW, TH = 320 * scale, 200 * scale
    pad, gap = 24, 16
    f_title = font(30, True); f_sub = font(15); f_lab = font(19, True)
    ncol = 2
    nrow = (len(tiles) + ncol - 1) // ncol
    Wpx = pad * 2 + ncol * TW + (ncol - 1) * gap
    Hpx = 104 + nrow * (TH + 34) + pad
    img = Image.new('RGB', (Wpx, Hpx), (18, 18, 22))
    d = ImageDraw.Draw(img)
    d.text((pad, 22), f'Another World v {COLS[col][0]} ({COLS[col][1]})', (240, 240, 245), f_title)
    d.text((pad, 60), 'takto by hra vyzerala na neupravenom Atari XL/XE - 4 farby z GTIA palety, '
                      'pixel 2:1, 96 riadkov po 2 skenlinky', (150, 150, 160), f_sub)
    for i, (label, sub, row) in enumerate(tiles):
        rr, cc = divmod(i, ncol)
        x = pad + cc * (TW + gap)
        y = 104 + rr * (TH + 34)
        big = np.repeat(np.repeat(row[col], scale, 0), scale, 1)
        img.paste(Image.fromarray(big), (x, y))
        d.rectangle([x - 1, y - 1, x + TW, y + TH], outline=(70, 70, 80))
        d.text((x, y + TH + 7), label, (225, 225, 232), f_lab)
    img.save(path)
    print('->', path, img.size)


if __name__ == '__main__':
    t = build()
    compose(t, os.path.join(HERE, 'aw_gr7_compare.png'))
    gallery(t, os.path.join(HERE, 'aw_gr7_gallery.png'), col=4, scale=2)
