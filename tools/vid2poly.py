#!/usr/bin/env python3
"""
vid2poly.py - convert an MP4 into Another-World-style flat polygons.

Pipeline (PC side, prototype):
  1. decode frames (imageio + bundled ffmpeg) at a low target fps -- the
     original AW intro ran at a low effective rate, so we decimate hard to
     keep the frame count (and the atari data) small.
  2. letterbox/resize each frame into the AW 320x200 page.
  3. quantize to <=16 colours (adaptive palette per frame, no dither -> flat
     "rotoscoped" look like AW).
  4. vectorise: split every colour region into Y-MONOTONE bands (one run per
     row) and Douglas-Peucker simplify the left/right edge of each band. A
     y-monotone band maps 1:1 onto an AW polygon (left chain top->bottom, then
     right chain bottom->top) so AW's trapezoid filler renders it exactly.
  5. render the polygons back THROUGH aw_sim.fill_poly_int (the 6502-faithful
     raster) and write a side-by-side preview [quantized target | polygons].

This proves the vectorisation is AW-renderable and lets us judge quality and
the polygon budget before wiring it to the playlist/poly binary + the ATR.

Usage:
    python tools/vid2poly.py                       # sample preview frames
    python tools/vid2poly.py --at 5,60,130,200     # preview at these seconds
    python tools/vid2poly.py --eps 1.5 --min-area 12 --colors 16
"""
import os, sys, argparse
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aw_sim
from aw_sim import W, H, SIZE, fill_poly_int, frame_to_rgb

OUT = os.path.join(os.path.dirname(HERE), 'out')
VIDEO = os.path.join(os.path.dirname(HERE), 'video', 'alien-polygons.mp4')


# ---------------------------------------------------------------------------
# frame access
# ---------------------------------------------------------------------------
def open_video(path):
    import imageio
    return imageio.get_reader(path)


def frame_at(reader, sec, src_fps):
    return reader.get_data(int(round(sec * src_fps)))


# ---------------------------------------------------------------------------
# 1080p RGB frame -> 320x200 indexed page + 16-colour RGB palette
# ---------------------------------------------------------------------------
def letterbox(rgb):
    """Original frame, untouched colours, fit into 320x200 (1:1 reference)."""
    img = Image.fromarray(rgb)
    sw, sh = img.size
    scale = min(W / sw, H / sh)
    nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new('RGB', (W, H), (0, 0, 0))
    canvas.paste(img, ((W - nw) // 2, (H - nh) // 2))
    return canvas


def quantize_frame(rgb, ncolors=16, blur=0.0, median=0):
    """Letterbox `rgb` (HxWx3 uint8) into 320x200, adaptive-quantize to
    <=ncolors. Returns (idx HxW uint8, palette list[(r,g,b)] len 16).
    `median` = edge-PRESERVING despeckle (keeps shapes crisp, kills noise);
    `blur` = gaussian smoothing (melts detail). Median is usually better."""
    from PIL import ImageFilter
    img = Image.fromarray(rgb)
    sw, sh = img.size
    scale = min(W / sw, H / sh)
    nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    if median and median >= 3:
        img = img.filter(ImageFilter.MedianFilter(median | 1))   # odd size
    if blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    canvas = Image.new('RGB', (W, H), (0, 0, 0))
    canvas.paste(img, ((W - nw) // 2, (H - nh) // 2))
    q = canvas.quantize(colors=ncolors, method=Image.MEDIANCUT, dither=Image.NONE)
    idx = np.asarray(q, dtype=np.uint8)
    pal_flat = q.getpalette()[:ncolors * 3]
    pal = [(pal_flat[i*3], pal_flat[i*3+1], pal_flat[i*3+2]) for i in range(ncolors)]
    while len(pal) < 16:
        pal.append((0, 0, 0))
    return idx, pal


# ---------------------------------------------------------------------------
# 3b. despeckle: absorb tiny colour blobs into their neighbours (edge-PRESERVING,
#     NO holes, NO blur). This is the right knob to cut polygon count: video
#     compression sprays 1-3px speckle around edges -> each becomes its own band.
#     We relabel any connected component smaller than `min_size` to the colour of
#     its nearest larger region (euclidean nearest), so the speckle merges into a
#     neighbour instead of spawning a polygon (min-area would punch a hole).
# ---------------------------------------------------------------------------
def despeckle(idx, min_size):
    if min_size <= 1:
        return idx
    from scipy import ndimage
    out = idx.copy()
    small = np.zeros(out.shape, dtype=bool)
    for c in range(16):
        m = (out == c)
        if not m.any():
            continue
        lab, n = ndimage.label(m)
        if n == 0:
            continue
        sizes = np.bincount(lab.reshape(-1))
        tiny = np.flatnonzero(sizes < min_size)
        tiny = tiny[tiny != 0]                  # 0 = background label
        if tiny.size:
            small |= np.isin(lab, tiny)
    if small.any():
        ind = ndimage.distance_transform_edt(small, return_distances=False,
                                             return_indices=True)
        out = out[tuple(ind)]                   # small px <- nearest non-small colour
    return out


# ---------------------------------------------------------------------------
# 4. vectorise a colour mask into y-monotone bands
# ---------------------------------------------------------------------------
def row_runs(maskrow):
    """maskrow: bool array length W -> list of (x0, x1) inclusive runs."""
    a = np.concatenate(([0], maskrow.view(np.int8), [0]))
    edges = np.flatnonzero(np.diff(a))
    return [(int(edges[k]), int(edges[k + 1]) - 1) for k in range(0, len(edges), 2)]


def decompose(mask):
    """mask: HxW bool -> list of bands. Each band is a vertical stack of
    single, x-overlapping runs (=> strictly y-monotone). Bands are split at
    every junction (run that splits/merges between rows)."""
    h, w = mask.shape
    finished, active = [], []   # band = {'top', 'left':[], 'right':[], 'last':(x0,x1)}
    for r in range(h):
        runs = row_runs(mask[r])
        used = [False] * len(runs)
        b_over = [[] for _ in active]
        r_over = [[] for _ in runs]
        for bi, b in enumerate(active):
            lx0, lx1 = b['last']
            for ri, (x0, x1) in enumerate(runs):
                if x0 <= lx1 and x1 >= lx0:
                    b_over[bi].append(ri)
                    r_over[ri].append(bi)
        nxt = []
        for bi, b in enumerate(active):
            if len(b_over[bi]) == 1:
                ri = b_over[bi][0]
                if len(r_over[ri]) == 1 and not used[ri]:
                    x0, x1 = runs[ri]
                    b['left'].append(x0)
                    b['right'].append(x1 + 1)   # exclusive right edge
                    b['last'] = (x0, x1)
                    used[ri] = True
                    nxt.append(b)
                    continue
            finished.append(b)                  # junction or no match -> close
        for ri, (x0, x1) in enumerate(runs):
            if not used[ri]:
                nxt.append({'top': r, 'left': [x0], 'right': [x1 + 1], 'last': (x0, x1)})
        active = nxt
    finished.extend(active)
    return finished


def dp_keep(points, eps, lo, hi, keep):
    """Douglas-Peucker; record KEPT indices (into `points`) in set `keep`.
    points: list of (x,y); [lo,hi] inclusive index range."""
    if hi <= lo + 1:
        return
    x0, y0 = points[lo]
    x1, y1 = points[hi]
    dx, dy = x1 - x0, y1 - y0
    nrm = (dx * dx + dy * dy) ** 0.5 or 1.0
    dmax, idx = 0.0, lo
    for i in range(lo + 1, hi):
        px, py = points[i]
        d = abs(dx * (y0 - py) - (x0 - px) * dy) / nrm
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        keep.add(idx)
        dp_keep(points, eps, lo, idx, keep)
        dp_keep(points, eps, idx, hi, keep)


def band_to_poly(b, eps):
    """A y-monotone band -> AW quad-strip polygon. AW's filler walks the LEFT
    and RIGHT edges in lock-step over equal segment heights, so both edges MUST
    have vertices at the SAME y levels. We DP-simplify each edge, take the
    UNION of the kept y-indices, and sample both edges there. The band covers
    rows top..top+n-1, so the polygon spans y = top .. top+n (extra bottom
    level closes the last row)."""
    n = len(b['left'])
    top = b['top']
    levels = n + 1                              # vertex rows: top .. top+n
    Lv = [b['left'][min(k, n - 1)] for k in range(levels)]
    Rv = [b['right'][min(k, n - 1)] for k in range(levels)]
    left = [(Lv[k], top + k) for k in range(levels)]
    right = [(Rv[k], top + k) for k in range(levels)]
    keep = {0, levels - 1}
    dp_keep(left, eps, 0, levels - 1, keep)
    dp_keep(right, eps, 0, levels - 1, keep)
    ks = sorted(keep)
    L = [left[k] for k in ks]
    R = [right[k] for k in ks]
    return L + R[::-1]                          # top->bot down left, bot->top up right


def vectorise(idx, eps, min_area, bg_color):
    """idx: HxW colour-index page -> list of (color, pts), largest first,
    skipping the background colour (drawn as a full-page fill)."""
    polys = []
    for c in range(16):
        if c == bg_color:
            continue
        mask = (idx == c)
        area = int(mask.sum())
        if area < min_area:
            continue
        for b in decompose(mask):
            ba = sum(b['right'][i] - b['left'][i] for i in range(len(b['left'])))
            if ba < min_area:
                continue
            polys.append((ba, c, band_to_poly(b, eps)))
    polys.sort(key=lambda t: -t[0])             # big regions first (background-ish)
    return [(c, pts) for _ba, c, pts in polys]


# ---------------------------------------------------------------------------
# render polygons through the AW raster
# ---------------------------------------------------------------------------
def render(polys, pal, bg_color):
    page = bytearray([bg_color]) * SIZE
    page0 = bytearray(SIZE)                      # no copy-from-bg colours used
    for c, pts in polys:
        fill_poly_int(page, page0, pts, c)
    return frame_to_rgb(page, pal)


def page_rgb_to_img(rgb):
    return Image.frombytes('RGB', (W, H), bytes(rgb))


def idx_to_img(idx, pal):
    lut = np.array(pal, dtype=np.uint8)
    return Image.fromarray(lut[idx])


# ---------------------------------------------------------------------------
def process_frame(rgb, args):
    idx, pal = quantize_frame(rgb, args.colors, args.blur, args.median)
    idx = despeckle(idx, args.despeckle)
    counts = np.bincount(idx.reshape(-1), minlength=16)
    bg = int(counts.argmax())
    polys = vectorise(idx, args.eps, args.min_area, bg)
    out_rgb = render(polys, pal, bg)
    return idx, pal, polys, out_rgb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--video', default=VIDEO)
    ap.add_argument('--at', default='5,60,130,200',
                    help='comma seconds to preview')
    ap.add_argument('--colors', type=int, default=16)
    ap.add_argument('--blur', type=float, default=0.0,
                    help='gaussian pre-blur radius (px) before quantize')
    ap.add_argument('--median', type=int, default=0,
                    help='edge-preserving median despeckle window (3,5,7..)')
    ap.add_argument('--despeckle', type=int, default=12,
                    help='absorb colour blobs smaller than N px into neighbour '
                         '(no holes); the right knob to cut polygon count')
    ap.add_argument('--eps', type=float, default=2.0,
                    help='Douglas-Peucker tolerance (px); higher = fewer points')
    ap.add_argument('--min-area', type=int, default=4,
                    help='drop bands smaller than this (keep SMALL: punches holes)')
    ap.add_argument('--scale', type=int, default=3, help='preview upscale')
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    rd = open_video(args.video)
    meta = rd.get_meta_data()
    src_fps = meta.get('fps', 24.0)
    secs = [float(s) for s in args.at.split(',') if s.strip()]

    for sec in secs:
        rgb = frame_at(rd, sec, src_fps)
        idx, pal, polys, out_rgb = process_frame(rgb, args)

        s = args.scale
        panels = [letterbox(rgb),                       # ORIGINAL 1:1
                  idx_to_img(idx, pal),                 # QUANTIZED target
                  page_rgb_to_img(out_rgb)]             # AW-POLYGON render
        big = [p.resize((W * s, H * s), Image.NEAREST) for p in panels]
        pad = 8 * s
        combo = Image.new('RGB', (big[0].width * 3 + pad * 2, big[0].height), (40, 40, 40))
        for k, b in enumerate(big):
            combo.paste(b, (k * (big[0].width + pad), 0))
        path = os.path.join(OUT, f'_vid2poly_{int(sec)}.png')
        combo.save(path)
        ncol = len(set(c for c, _ in polys))
        print(f't={sec:6.1f}s  polys={len(polys):5d}  colours_used={ncol:2d}  -> {os.path.relpath(path)}')

    rd.close()
    print('\nPanels: ORIGINAL | QUANTIZED | AW-POLYGON render')


if __name__ == '__main__':
    main()
