#!/usr/bin/env python3
"""
vid2poly_build.py - encode an MP4 into AW playlist+poly+palette binaries and
build a standalone XEX that the UNMODIFIED Another World intro engine replays.

It writes out/alien_playlist.bin + out/alien_poly.bin + out/alien_pal.bin in the
EXACT format src/aw_replayer.asm reads, then assembles alien/alien.asm (a copy of
the intro spine that includes alien/alien_data.asm instead of src/aw_data.asm).
The src/ engine and the original out/intro_*.bin are NOT touched.

VRAM budget (fixed by the intro engine, see src/aw_data.asm):
  poly  blob  <= 65230 bytes  (4 banks; last `ins` reads exactly 16078 B)
  playlist    <= ~114688 and >= 98304 (7 banks; 6 fixed 16K reads + remainder)
  palette     = 32 x 16 x RGB(7-bit) = 1536 bytes
Video shapes don't repeat, so the poly blob fills fast -> this is a SHORT clip
(a few seconds). A long video needs ATR streaming (the game's mechanism), TODO.

Per-frame ADAPTIVE palette: each frame gets its own 16-colour palette in slot
`frame_idx` (<=32 frames) and emits SETPAL frame_idx -> best quality.

Usage:
    python tools/vid2poly_build.py --video video/alien-polygons.mp4 --start 0
    python tools/vid2poly_build.py --fps 12 --colors 16 --despeckle 24 --eps 2.5
    python tools/vid2poly_build.py --no-build      # encode only, skip mads
"""
import os, sys, argparse, struct, subprocess, shutil
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import vid2poly as v2p

OUT = os.path.join(PROJ, 'out')
POLY_CAP = 65230
PLAY_MIN = 98304
PLAY_CAP = 114000          # under the 7-bank 114688 ceiling
MAX_PALS = 32

OPC = dict(pal=0x01, sel=0x02, fill=0x03, copy=0x04, poly=0x05, blit=0x06, end=0x00)


# ---------------------------------------------------------------------------
# vectorise into AW-encodable polys: split into <=160px vertical strips so every
# polygon's bbox fits in a byte (coords/bbw/bbh are bytes, 0..255).
# ---------------------------------------------------------------------------
def vectorise_strips(idx, eps, min_area, bg, strip=160):
    h, w = idx.shape
    polys = []
    for xa in range(0, w, strip):
        xb = min(w, xa + strip)
        for c in range(16):
            if c == bg:
                continue
            m = (idx == c)
            if not m.any():
                continue
            m = m.copy()
            m[:, :xa] = False
            m[:, xb:] = False
            if m.sum() < min_area:
                continue
            for b in v2p.decompose(m):
                ba = sum(b['right'][i] - b['left'][i] for i in range(len(b['left'])))
                if ba < min_area:
                    continue
                pts = v2p.band_to_poly(b, eps)
                polys.append((ba, c, pts))
    polys.sort(key=lambda t: -t[0])
    return [(c, pts) for _ba, c, pts in polys]


def encode_poly(color, pts):
    """One AW fill record: 0xC0|col, bbw, bbh, n, then n*(dx,dy) bytes.
    Returns (record_bytes, cx, cy) or None if it can't be byte-encoded."""
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    bbw, bbh = maxx - minx, maxy - miny
    if bbw > 255 or bbh > 255 or len(pts) > 255:
        return None
    rec = bytearray([0xC0 | (color & 0x3F), bbw, bbh, len(pts)])
    for (x, y) in pts:
        rec.append((x - minx) & 0xFF)
        rec.append((y - miny) & 0xFF)
    cx = minx + bbw // 2
    cy = miny + bbh // 2
    return bytes(rec), cx, cy


def pl_drawpoly(off, x, y, zoom=64):
    return bytes([OPC['poly']]) + struct.pack('<HHHH', off & 0xFFFF, x & 0xFFFF,
                                              y & 0xFFFF, zoom & 0xFFFF)


# ---------------------------------------------------------------------------
def encode_clip(args):
    rd = v2p.open_video(args.video)
    meta = rd.get_meta_data()
    src_fps = meta.get('fps', 24.0)
    duration = meta.get('duration', 60.0)
    hold = max(1, round(50.0 / args.fps))          # PAL vblanks per frame
    n_avail = int((duration - args.start) * args.fps)
    n_avail = min(n_avail, MAX_PALS)                # one palette slot per frame

    poly_blob = bytearray()
    poly_off = {}                                   # record bytes -> offset (dedup)
    playlist = bytearray()
    pals = []                                       # per-frame 16-colour palettes
    dropped = 0
    frames_done = 0

    for fi in range(n_avail):
        sec = args.start + fi / args.fps
        rgb = rd.get_data(int(round(sec * src_fps)))
        idx, pal, canvas = v2p.quantize_frame(
            rgb, args.colors, 0.0, args.median, args.meanshift, args.sr,
            args.bilateral, args.contrast, args.saturation, args.brightness)
        if args.lowpoly > 0:
            polys = v2p.vectorise_lowpoly(canvas, idx, args.lowpoly,
                                          args.canny_lo, args.canny_hi)
            bg = 0
        else:
            idx = v2p.despeckle(idx, args.despeckle)
            bg = int(np.bincount(idx.reshape(-1), minlength=16).argmax())
            polys = vectorise_strips(idx, args.eps, args.min_area, bg)

        # build this frame's command bytes into a scratch buffer first, so we
        # only commit the frame if BOTH blobs still fit.
        frame_cmds = bytearray()
        frame_cmds += bytes([OPC['pal'], fi])               # SETPAL <slot>
        frame_cmds += bytes([OPC['sel'], fi & 1])           # draw into page 0/1
        frame_cmds += bytes([OPC['fill'], fi & 1, bg])      # clear it to bg
        new_records = bytearray()
        local_off = dict(poly_off)
        poly_full = False
        for c, pts in polys:
            enc = encode_poly(c, pts)
            if enc is None:
                dropped += 1
                continue
            rec, cx, cy = enc
            off = local_off.get(rec)
            if off is None:
                off = len(poly_blob) + len(new_records)
                if off + len(rec) > POLY_CAP:
                    poly_full = True                        # blob full -> end clip
                    break
                local_off[rec] = off
                new_records += rec
            frame_cmds += pl_drawpoly(off, cx, cy)
        frame_cmds += bytes([OPC['blit'], fi & 1, hold])    # show, hold N vblanks

        # commit only WHOLE frames: if this one couldn't fit completely, stop the
        # clip BEFORE it (a half-drawn frame would render broken).
        if (poly_full or
                len(playlist) + len(frame_cmds) + 1 > PLAY_CAP):
            break
        poly_blob += new_records
        poly_off = local_off
        playlist += frame_cmds
        pals.append(pal)
        frames_done += 1
        if fi % 5 == 0 or fi == n_avail - 1:
            print(f'  frame {fi:3d}  polys/frame~{len(polys):4d}  '
                  f'poly={len(poly_blob):6d}/{POLY_CAP}  pl={len(playlist):6d}')

    rd.close()
    playlist += bytes([OPC['end']])

    # pad to the sizes aw_data.asm's fixed `ins` reads require
    if len(poly_blob) < POLY_CAP:
        poly_blob += bytes(POLY_CAP - len(poly_blob))
    if len(playlist) < PLAY_MIN:
        playlist += bytes(PLAY_MIN - len(playlist))

    # palette file: 32 slots x 16 colours x RGB, 7-bit (engine asl's to 8-bit)
    palbuf = bytearray(MAX_PALS * 16 * 3)
    for k, pal in enumerate(pals):
        for i in range(16):
            r, g, b = pal[i] if i < len(pal) else (0, 0, 0)
            o = k * 48 + i * 3
            palbuf[o] = r >> 1; palbuf[o+1] = g >> 1; palbuf[o+2] = b >> 1

    open(os.path.join(OUT, f'{args.name}_poly.bin'), 'wb').write(poly_blob)
    open(os.path.join(OUT, f'{args.name}_playlist.bin'), 'wb').write(playlist)
    open(os.path.join(OUT, f'{args.name}_pal.bin'), 'wb').write(palbuf)

    print(f'\nencoded {frames_done} frames ({frames_done/args.fps:.2f}s @ {args.fps}fps, '
          f'hold={hold} vbl)')
    print(f'  poly     : {len(poly_blob)} bytes (cap {POLY_CAP})')
    print(f'  playlist : {len(playlist)} bytes (cap {PLAY_CAP})')
    print(f'  palette  : {len(palbuf)} bytes   ({len(pals)} slots used)')
    if dropped:
        print(f'  dropped  : {dropped} un-encodable polys (bbox/vertex overflow)')
    return frames_done


# ---------------------------------------------------------------------------
# generate per-video build files (copies of the intro spine + data include, with
# the ins paths repointed to out/<name>_*.bin). src/ and out/intro_*.bin are
# NEVER touched. Reusable for any number of videos -- one set of files per name.
# ---------------------------------------------------------------------------
BUILDDIR = 'vidbuild'                      # root-relative; mads runs from PROJ


def gen_build_files(name):
    adir = os.path.join(PROJ, BUILDDIR)
    os.makedirs(adir, exist_ok=True)

    spine = open(os.path.join(PROJ, 'src', 'awvbxe.asm'), encoding='ascii').read()
    spine = spine.replace("icl 'src/aw_data.asm'",
                          f"icl '{BUILDDIR}/{name}_data.asm'")
    open(os.path.join(adir, f'{name}.asm'), 'w', encoding='ascii').write(spine)

    data = open(os.path.join(PROJ, 'src', 'aw_data.asm'), encoding='ascii').read()
    data = (data.replace("'out/intro_pal.bin'", f"'out/{name}_pal.bin'")
                .replace("'out/intro_poly.bin'", f"'out/{name}_poly.bin'")
                .replace("'out/intro_playlist.bin'", f"'out/{name}_playlist.bin'"))
    open(os.path.join(adir, f'{name}_data.asm'), 'w', encoding='ascii').write(data)


def build_xex(name):
    mads = os.path.join(PROJ, 'mads.exe')
    if not os.path.exists(mads):
        mads = shutil.which('mads') or 'mads.exe'
    xex = os.path.join(PROJ, f'{name}.xex')
    r = subprocess.run([mads, f'{BUILDDIR}/{name}.asm', f'-o:{name}.xex'],
                       cwd=PROJ, capture_output=True, text=True)
    print(r.stdout[-1500:])
    if r.returncode != 0:
        print('--- mads stderr ---'); print(r.stderr[-1500:])
        print('BUILD FAILED'); return False
    print(f'BUILD OK -> {xex}  ({os.path.getsize(xex)} bytes)')
    return True


def sanitize(stem):
    s = ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in stem.lower())
    return s.strip('_-') or 'video'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--video', default=v2p.VIDEO)
    ap.add_argument('--name', default=None,
                    help='build name (default = video filename); picks '
                         'out/<name>_*.bin, vidbuild/<name>.asm, <name>.xex')
    ap.add_argument('--start', type=float, default=0.0, help='start second')
    ap.add_argument('--fps', type=float, default=12.0)
    ap.add_argument('--colors', type=int, default=16)
    ap.add_argument('--despeckle', type=int, default=24)
    ap.add_argument('--eps', type=float, default=2.5)
    ap.add_argument('--min-area', type=int, default=6)
    ap.add_argument('--median', type=int, default=0)
    # stylize (AW look) + low-poly Delaunay
    ap.add_argument('--meanshift', type=int, default=0)
    ap.add_argument('--sr', type=int, default=0)
    ap.add_argument('--bilateral', type=int, default=0)
    ap.add_argument('--contrast', type=float, default=1.0)
    ap.add_argument('--saturation', type=float, default=1.0)
    ap.add_argument('--brightness', type=float, default=1.0)
    ap.add_argument('--lowpoly', type=int, default=0,
                    help='Delaunay low-poly: target sample points (triangles ~2x)')
    ap.add_argument('--canny-lo', type=int, default=40)
    ap.add_argument('--canny-hi', type=int, default=120)
    ap.add_argument('--no-build', action='store_true')
    args = ap.parse_args()
    if not args.name:
        args.name = sanitize(os.path.splitext(os.path.basename(args.video))[0])

    os.makedirs(OUT, exist_ok=True)
    print(f'build name: {args.name}   video: {args.video}')
    n = encode_clip(args)
    if n == 0:
        print('no frames encoded'); return
    gen_build_files(args.name)
    if not args.no_build:
        build_xex(args.name)


if __name__ == '__main__':
    main()
