#!/usr/bin/env python3
"""delta_size.py - could the intro be a pre-rendered 2bpp DELTA STREAM instead
of live polygon rasterisation?

With 4 colours a full screen is 4000 B and consecutive frames differ little;
if the whole intro's deltas fit the extended RAM that poly+playlist occupy
today (11 banks = 180 KB), the renderer becomes a byte copier and the frame
rate is bounded by dirty bytes alone (~10 cyc/B), not by edge walking.

Replays the exact hoisted playlist (like measure_pal.py), builds the SHOWN
2bpp byte page at every BLIT through the CURRENT tables, and measures per
frame: changed bytes vs the previous shown frame, and an RLE-delta estimate
(3 B header per run of changed bytes + the bytes; runs merged across gaps < 4
-- a real encoder would do at least this well).

    python gr7/delta_size.py
"""
import os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PROJ, 'tools'))
import aw_sim
import measure_pal as M

W, H = 320, 200
OPERANDS = M.OPERANDS


def main():
    play = open(M.PLAYLIST, 'rb').read()
    polybin = open(M.POLY, 'rb').read()
    lut, reg = M.load_tables()
    bright = M.load_bright()
    pages = [M._Planes() for _ in range(4)]
    regmap = {id(p.idx): p for p in pages}
    cur = [0]

    def span2(page, page0, row, xa, xb, color):
        s = slice(row + xa, row + xb + 1)
        n = xb - xa + 1
        pl = regmap[id(page)]
        if color < 0x10:
            page[s] = bytes([color]) * n
            pl.tag[s] = bytes([cur[0]]) * n
            pl.trn[s] = b'\0' * n
            pl.uix[s] = page[s]
        elif color == 0x10:
            pl.uix[s] = page[s]
            for q in range(row + xa, row + xb + 1):
                page[q] |= 0x08
            pl.tag[s] = bytes([cur[0]]) * n
            pl.trn[s] = b'\1' * n
        else:
            p0 = regmap[id(page0)]
            page[s] = page0[s]
            pl.tag[s] = p0.tag[s]
            pl.trn[s] = p0.trn[s]
            pl.uix[s] = p0.uix[s]
    aw_sim._span = span2
    poly = aw_sim.PolyData(polybin, aw_sim.fill_poly_int)
    cur_draw = 0
    fill_aw = [0, 0, 0, 0]

    prev = np.zeros(4000, np.uint8)
    changed, rle, frames = [], [], 0
    i, n = 0, len(play)
    while i < n:
        op = play[i]; i += 1
        na = OPERANDS.get(op)
        if op == 0x00:
            break
        a = play[i:i + na]; i += na
        if op == 0x01:
            cur[0] = a[0] & 0x1F
            for k in range(4):
                if fill_aw[k] is not None:
                    pages[k].tag[:] = bytes([cur[0]]) * (W * H)
        elif op == 0x02:
            cur_draw = a[0] & 3
        elif op == 0x03:
            k = a[0] & 3; col = a[1] & 0x0F
            pages[k].idx[:] = bytes([col]) * (W * H)
            pages[k].tag[:] = bytes([cur[0]]) * (W * H)
            pages[k].trn[:] = bytes(W * H)
            pages[k].uix[:] = pages[k].idx
            fill_aw[k] = col
        elif op == 0x04:
            s = a[0] & 3; d = a[1] & 3
            if s != d:
                pages[d].idx[:] = pages[s].idx
                pages[d].tag[:] = pages[s].tag
                pages[d].trn[:] = pages[s].trn
                pages[d].uix[:] = pages[s].uix
            fill_aw[d] = None
        elif op == 0x05:
            off = a[0] | (a[1] << 8)
            x = ((a[2] | (a[3] << 8)) ^ 0x8000) - 0x8000
            y = ((a[4] | (a[5] << 8)) ^ 0x8000) - 0x8000
            zoom = a[6] | (a[7] << 8)
            poly.page0 = pages[0].idx
            poly.draw(pages[cur_draw].idx, off, x, y, zoom, 0xFF)
            fill_aw[cur_draw] = None
        elif op == 0x06:
            pg = pages[a[0] & 3]
            idx = np.frombuffer(bytes(pg.idx), np.uint8).reshape(H, W)[::2, ::2]
            tag = np.frombuffer(bytes(pg.tag), np.uint8).reshape(H, W)[::2, ::2]
            trn = np.frombuffer(bytes(pg.trn), np.uint8).reshape(H, W)[::2, ::2]
            uix = np.frombuffer(bytes(pg.uix), np.uint8).reshape(H, W)[::2, ::2]
            code = np.where(trn.astype(bool),
                            bright[tag, lut[tag, uix & 15]], lut[tag, idx])
            b = np.zeros((100, 40), np.uint8)
            for k in range(4):
                b |= ((code[:, k::4] & 3) << (6 - 2 * k)).astype(np.uint8)
            page = b.ravel()
            diff = page != prev
            nch = int(diff.sum())
            changed.append(nch)
            if nch:
                # merge runs separated by <4 clean bytes, 3 B header per run
                pos = np.flatnonzero(diff)
                splits = np.flatnonzero(np.diff(pos) > 4)
                nruns = len(splits) + 1
                spans = np.split(pos, splits + 1)
                payload = sum(int(s[-1] - s[0] + 1) for s in spans)
                rle.append(3 * nruns + payload)
            else:
                rle.append(1)                       # "no change" marker
            prev = page.copy()
            frames += 1
            if frames % 500 == 0:
                print(f'  ... {frames}')
    ch = np.array(changed); rl = np.array(rle)
    print(f'{frames} snimok')
    print(f'zmenene bajty/snimka: median {int(np.median(ch))},'
          f' priemer {ch.mean():.0f}, p90 {int(np.percentile(ch, 90))},'
          f' max {int(ch.max())}')
    print(f'RLE delta stream: {rl.sum()} B celkovo ({rl.sum()/1024:.0f} KB)')
    print(f'  dnes zabera poly+playlist {(65230 + 107933)/1024:.0f} KB v 11'
          f' bankach; vsetkych 16 bank = 256 KB (zvuk berie 80 KB)')
    print(f'CPU odhad prehravaca: median {int(np.median(rl))*10} cyk/snimku'
          f' (~10 cyk/B) vs dnesnych ~137000')


if __name__ == '__main__':
    main()
