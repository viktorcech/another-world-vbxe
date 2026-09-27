#!/usr/bin/env python3
"""gr7lib.py - shared helpers for the "Another World in GRAPHICS 7" mock-ups.

Frames come from the project's own PC oracle (tools/aw_sim.py + tools/game_sim.py),
so every pixel here is a REAL Another World frame, rasterised exactly the way the
6502 raster does it.  The only thing this module adds is the mode conversion:

  * 320x200 / 16 colours          - the PC original (and what the VM produces)
  * 160x200 / 16 colours          - today's VBXE port (LORES, 1 byte per pixel)
  * 160x192 / 4 colours (ANTIC E) - "GRAPHICS 15" (a.k.a. 7+)
  * 160x96  / 4 colours (ANTIC D) - "GRAPHICS 7"
  * ... plus DLI band variants, where the 4 registers are re-loaded per band.

The 16 -> 4 mapping is computed PER AW PALETTE (not per frame) from the pixel
histogram of every frame that uses that palette, because that is what a real port
would bake in at build time: 32 palettes x 4 colour-register values per part.
"""
import os, sys, collections
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, HERE)
from atari_pal import PALETTE, DISTINCT, DISTINCT_RGB

W, H = 320, 200


# ---------------------------------------------------------------------------
# colour distance (redmean - cheap perceptual approximation)
# ---------------------------------------------------------------------------
def redmean_dist(a, b, chroma_weight=1.5):
    """a:(n,3) b:(m,3) -> (n,m) squared perceptual distance, CHROMA-WEIGHTED.

    Plain least-squares RGB (or redmean) quantisation has a well known failure
    mode: when a palette holds several distinct hues, a neutral grey minimises
    the total squared error better than any of them, so a 4-colour fit collapses
    pinks, reds and cyans into greys. That is exactly what happened to the later
    intro scenes. Splitting the error into luma and chroma and weighting chroma
    up makes a grey an expensive substitute for a colour, which is what the eye
    actually thinks.

    The weight is 1.5, picked by sweeping it against the PIXEL-WEIGHTED error over
    the whole intro: 1.0 scores 27.35 and 1.5 scores 27.47 (half a percent worse),
    but 1.5 keeps hues that 1.0 flattens to grey. 3.0 costs 30.6 and starts wrecking
    the smooth blue ramps of the opening scenes, so it is too far.
    """
    a = np.asarray(a, np.float64)[:, None, :]
    b = np.asarray(b, np.float64)[None, :, :]
    ay = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    by = 0.299 * b[..., 0] + 0.587 * b[..., 1] + 0.114 * b[..., 2]
    dy = ay - by
    dcb = (a[..., 2] - ay) - (b[..., 2] - by)
    dcr = (a[..., 0] - ay) - (b[..., 0] - by)
    return dy * dy + chroma_weight * (dcb * dcb + dcr * dcr)


def quantise(src_rgb, weights, k=4, restarts=40, seed=1):
    """Pick k Atari colour-register values that best represent the weighted set of
    source colours.  k-medoids over the 128 physically distinct GTIA colours.
    Returns (regs[k], assign[n]) - assign maps each source colour to a register slot."""
    rng = np.random.default_rng(seed)
    D = redmean_dist(np.asarray(src_rgb, float), DISTINCT_RGB.astype(float))   # (n,128)
    w = np.asarray(weights, float)
    n = len(w)
    if n <= k:
        chosen = [int(np.argmin(D[i])) for i in range(n)]
        chosen += [chosen[-1] if chosen else 0] * (k - n)
        return [int(DISTINCT[c]) for c in chosen[:k]], list(range(n))
    best = (None, np.inf)
    for r in range(restarts):
        # k-means++ style seeding on the candidate set
        cur = [int(np.argmin((D * w[:, None]).sum(0)))] if r == 0 else \
              [int(rng.integers(0, 128))]
        while len(cur) < k:
            dmin = D[:, cur].min(1)
            p = w * dmin
            p = p / p.sum() if p.sum() > 0 else np.full(n, 1 / n)
            pick = int(rng.choice(n, p=p))
            cur.append(int(np.argmin(D[pick])))
        cur = list(dict.fromkeys(cur))
        while len(cur) < k:
            cur.append(int(rng.integers(0, 128)))
        for _ in range(24):
            assign = np.argmin(D[:, cur], axis=1)
            new = list(cur)
            for c in range(k):
                m = assign == c
                if not m.any():
                    continue
                new[c] = int(np.argmin((D[m] * w[m, None]).sum(0)))
            if new == cur:
                break
            cur = new
        cost = float((w * D[:, cur].min(1)).sum())
        if cost < best[1]:
            best = (list(cur), cost)
    cur = best[0]
    assign = np.argmin(D[:, cur], axis=1)
    return [int(DISTINCT[c]) for c in cur], list(assign)


def palette_lut(aw_pal, counts, k=4, bands=1, band_counts=None):
    """Build the 16 -> k LUT(s) for one AW palette.
    counts: length-16 pixel histogram (aggregated over every frame using it).
    Returns (regs, lut) or, when bands>1, (regs_per_band, lut_per_band)."""
    src = np.array(aw_pal, float)
    def one(cnt):
        used = [i for i in range(16) if cnt[i] > 0]
        if not used:
            used = [0]
        regs, assign = quantise(src[used], [cnt[i] for i in used], k)
        lut = np.zeros(16, np.uint8)
        for u, a in zip(used, assign):
            lut[u] = a
        # unused indices: nearest of the chosen registers
        D = redmean_dist(src, PALETTE[regs].astype(float))
        for i in range(16):
            if cnt[i] == 0:
                lut[i] = int(np.argmin(D[i]))
        return regs, lut
    if bands == 1:
        return one(counts)
    out_regs, out_luts = [], []
    for b in range(bands):
        r, l = one(band_counts[b])
        out_regs.append(r); out_luts.append(l)
    return out_regs, out_luts


# ---------------------------------------------------------------------------
# mode renderers : page (320x200 indices) -> RGB image, already stretched to the
# 320x200-ish display box so the tiles are directly comparable
# ---------------------------------------------------------------------------
def _page(page):
    return np.frombuffer(bytes(page), np.uint8).reshape(H, W)


def render_original(page, aw_pal):
    return np.array(aw_pal, np.uint8)[_page(page)]


def render_vbxe_lr(page, aw_pal):
    """Today's port: 160x200 chunky, 16 (24-bit) colours -> stretched back to 320."""
    small = _page(page)[:, ::2]
    return np.repeat(np.array(aw_pal, np.uint8)[small], 2, axis=1)


def render_antic(page, regs, lut, rows, dli_bands=None):
    """rows=192 -> ANTIC E (GR.15);  rows=96 -> ANTIC D (GR.7).
    dli_bands: list of (regs, lut) per band when the DL re-loads the registers."""
    src = _page(page)
    ys = (np.arange(rows) * H // rows)                 # nearest row (what a half-res raster does)
    small = src[ys][:, ::2]                            # 160 wide
    if dli_bands is None:
        idx = lut[small]
        rgb = PALETTE[np.array(regs, np.uint8)][idx]
    else:
        rgb = np.zeros((rows, 160, 3), np.uint8)
        nb = len(dli_bands)
        for b, (r, l) in enumerate(dli_bands):
            y0, y1 = b * rows // nb, (b + 1) * rows // nb
            rgb[y0:y1] = PALETTE[np.array(r, np.uint8)][l[small[y0:y1]]]
    rgb = np.repeat(rgb, 2, axis=1)                    # 160 -> 320 (2:1 pixel aspect)
    if rows != 192:
        rgb = np.repeat(rgb, 192 // rows, axis=0)      # GR.7 row = 2 scanlines
    return rgb


def pad200(img):
    """192-line playfield centred in a 200-line comparison box."""
    if img.shape[0] == 200:
        return img
    out = np.zeros((200, img.shape[1], 3), np.uint8)
    out[4:4 + img.shape[0]] = img
    return out


# ---------------------------------------------------------------------------
# GTIA modes: 80 pixels wide (ANTIC F fetch, 4 bits per pixel)
# ---------------------------------------------------------------------------
def render_gr10(page, regs, lut, rows=192):
    """GRAPHICS 10: 80x192, 9 colours (COLPM0-3 + COLPF0-3 + COLBK)."""
    src = _page(page)
    ys = np.arange(rows) * H // rows
    small = src[ys][:, ::4]                            # 80 wide
    rgb = PALETTE[np.array(regs, np.uint8)][lut[small]]
    return np.repeat(rgb, 4, axis=1)                   # 4:1 pixel aspect -> 320


def gr9_lut(aw_pal, counts):
    """GRAPHICS 9: 80x192, 16 luminances of ONE hue (hue comes from COLBK).
    Pick the hue whose 16-step luma ramp fits the palette best."""
    src = np.array(aw_pal, float)
    w = np.asarray(counts, float)
    best = (None, np.inf, None)
    for hue in range(16):
        cand = PALETTE[hue * 16:hue * 16 + 16].astype(float)
        D = redmean_dist(src, cand)
        lut = np.argmin(D, axis=1).astype(np.uint8)
        cost = float((w * D.min(1)).sum())
        if cost < best[1]:
            best = (hue, cost, lut)
    hue, _, lut = best
    return hue, lut


def render_gr9(page, hue, lut, rows=192):
    src = _page(page)
    ys = np.arange(rows) * H // rows
    small = src[ys][:, ::4]
    ramp = PALETTE[hue * 16:hue * 16 + 16]
    return np.repeat(ramp[lut[small]], 4, axis=1)
