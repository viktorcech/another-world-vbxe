#!/usr/bin/env python3
"""make_design.py - the proposed GR.7 screen: ANTIC D playfield + ANTIC 2 subtitles.

One display list mixes both modes; a DLI on each band boundary reloads the four
colour registers.  AW's own text is already 40 columns of 8x8 glyphs on a 320-pixel
screen, i.e. EXACTLY ANTIC mode 2 - so the subtitles come out pixel-perfect, better
than in today's 160-wide VBXE build where they are squeezed to half width.
"""
import os, sys, collections
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import aw_sim, game_sim, aw_text
import gr7lib as G
from gr7lib import W, H, PALETTE
from make_mockup import collect, font, BANDS

ROWS = 96                       # ANTIC D rows (2 scanlines each)
TXT_BG, TXT_FG = 0x00, 0x0E     # COLPF2 = black band, COLPF1 luma -> white text


def antic2_strip(lines, cols=40):
    """Render text rows the way ANTIC mode 2 does: 320 px wide, 8x8 glyphs,
    COLPF2 background + COLPF1 luminance."""
    img = np.zeros((8 * len(lines), 320, 3), np.uint8)
    img[:, :] = PALETTE[TXT_BG]
    fg = PALETTE[TXT_FG]
    for r, (col, s) in enumerate(lines):
        for c, ch in enumerate(s[:cols - col]):
            o = ord(ch)
            if not (0x20 <= o <= 0x7F):
                continue
            g = (o - 0x20) * 8
            for j in range(8):
                bits = aw_text.FONT[g + j]
                for i in range(8):
                    if bits & (0x80 >> i):
                        img[r * 8 + j, (col + c) * 8 + i] = fg
    return img


def text_lines(dl):
    out = []
    for e in dl:
        if e[0] == 'txt':
            _, sid, x, y, colr = e
            s = aw_text.STRINGS.get(sid, '')
            for k, ln in enumerate(s.split('\n')):
                out.append((x, y + k * 8, ln))
    return out


def build_screen(page, aw_pal, hist, bh, dl):
    """-> RGB image of the whole 320x(2*ROWS) screen with mode-2 rows spliced in."""
    regs, lut = G.palette_lut(aw_pal, hist, 4)
    bregs, bluts = G.palette_lut(aw_pal, hist, 4, BANDS, bh)
    pf = G.render_antic(page, regs, lut, ROWS, list(zip(bregs, bluts)))   # 320x192
    tl = text_lines(dl)
    if not tl:
        return pf, []
    # each mode-2 row is 8 scanlines = 4 ANTIC D rows; snap to that grid
    rows = {}
    for x, y, s in tl:
        r = int(y * ROWS / H) // 4 * 4              # ANTIC D row where the line starts
        # scanline span the same text occupies in the 96-row bitmap (to fully cover it)
        lo, hi = int(y * ROWS / H), int((y + 7) * ROWS / H)
        rows.setdefault(r, [[], lo, hi])
        rows[r][0].append((x, s))
        rows[r][1] = min(rows[r][1], lo); rows[r][2] = max(rows[r][2], hi)
    strip_rows = []
    for r in sorted(rows):
        lines, lo, hi = rows[r]
        img = antic2_strip(lines)
        h = max(img.shape[0], (hi - min(r, lo) + 1) * 2)
        band = np.zeros((h, 320, 3), np.uint8); band[:, :] = PALETTE[TXT_BG]
        band[:img.shape[0]] = img
        y0 = min(min(r, lo) * 2, pf.shape[0] - h)
        pf[y0:y0 + h] = band
        strip_rows.append(r)
    return pf, strip_rows


DL_TEXT = [
    ('$70 $70 $70',        '24 prazdnych skenliniek (vertikalne centrovanie)'),
    ('$4D  lo hi',         'ANTIC D + LMS -> zaciatok kreslenej stranky'),
    ('$0D x3',             'dalsie riadky ANTIC D (160x2, 4 farby)'),
    ('$8D',                'ANTIC D s DLI -> prepis COLBK/COLPF0..2 pre dalsie pasmo'),
    ('...',                '... 96 riadkov ANTIC D spolu = 192 skenliniek'),
    ('$42  lo hi',         'ANTIC 2 + LMS -> riadok titulkov (40 znakov, CHBASE = font AW)'),
    ('$02',                'dalsi riadok titulkov (8 skenliniek = 4 riadky ANTIC D)'),
    ('$41  lo hi',         'JVB -> skok spat na zaciatok DL + cakanie na VBLANK'),
]


def main():
    scenes = [('intro', None, 1180), ('water', 16002, 115)]
    shots = []
    for name, part, fidx in scenes:
        frames, pals, hist, bh = collect(part, max(300, fidx + 10))
        page, pal, hold, draws, dl = frames[min(fidx, len(frames) - 1)]
        naive = G.render_antic(page, *G.palette_lut(pals[pal], hist[pal], 4), ROWS)
        good, _ = build_screen(page, pals[pal], hist[pal], bh[pal], dl)
        shots.append((name, naive, good, text_lines(dl)))
        print(f'  {name} f{fidx}: text = {[t[2] for t in text_lines(dl)]}')

    f_t = font(31, True); f_h = font(20, True); f_s = font(15); f_m = font(15)
    try:
        f_mono = ImageFont.truetype(r'C:\Windows\Fonts\consola.ttf', 15)
    except Exception:
        f_mono = f_m
    TW, TH = 640, 384
    Wpx, Hpx = 28 * 2 + TW * 2 + 30, 150 + len(shots) * (TH + 62) + 330
    img = Image.new('RGB', (Wpx, Hpx), (18, 18, 22))
    d = ImageDraw.Draw(img)
    d.text((28, 24), 'Navrh obrazovky: ANTIC D (GR.7) + ANTIC 2 titulky v jednom display liste',
           (240, 240, 245), f_t)
    for i, ln in enumerate([
        'Text v Another World je uz teraz 40 stlpcov x 8x8 glyfov na 320-bodovej obrazovke - to je PRESNE ANTIC mod 2.',
        'Staci mu v display liste vyhradit riadky (1 riadok modu 2 = 8 skenliniek = presne 4 riadky modu D) a CHBASE nasmerovat na font hry.']):
        d.text((28, 66 + i * 21), ln, (150, 150, 160), f_s)
    y = 146
    for name, naive, good, tl in shots:
        d.text((28, y - 26), f'{name.upper()}  -  text kresleny do bitmapy 160x96 (dnesny sposob)', (255, 160, 140), f_h)
        d.text((28 + TW + 30, y - 26), f'{name.upper()}  -  titulky v ANTIC 2 (320 bodov, ostre)', (150, 230, 170), f_h)
        for k, im in enumerate((naive, good)):
            big = np.repeat(np.repeat(im, 2, 0), 2, 1)
            x = 28 + k * (TW + 30)
            img.paste(Image.fromarray(big), (x, y))
            d.rectangle([x - 1, y - 1, x + big.shape[1], y + big.shape[0]], outline=(70, 70, 80))
        y += TH + 62
    d.text((28, y - 20), 'Display list (jeden ramec):', (240, 240, 245), f_h)
    for i, (code, cmt) in enumerate(DL_TEXT):
        d.text((28, y + 14 + i * 23), code, (255, 210, 120), f_mono)
        d.text((200, y + 14 + i * 23), cmt, (185, 185, 195), f_m)
    y2 = y + 14 + len(DL_TEXT) * 23 + 16
    for i, ln in enumerate([
        'DLI (bit 7 v instrukcii DL) prepise 4 farebne registre na hranici pasma -> viac ako 4 farby na obrazovke.',
        'VBI prepise LMS adresu -> prehodenie stranky je zadarmo (ziadne kopirovanie pamate).',
        'ANTIC 2 riadok stoji 40 bajtov DMA na skenlinku, font moze byt v ROM ($E000) alebo vlastny ($400 B).']):
        d.text((28, y2 + i * 22), '- ' + ln, (150, 150, 160), f_s)
    p = os.path.join(HERE, 'aw_gr7_design.png')
    img.save(p); print('->', p, img.size)


if __name__ == '__main__':
    main()
