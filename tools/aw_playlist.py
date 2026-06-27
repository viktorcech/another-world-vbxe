#!/usr/bin/env python3
"""
aw_playlist.py - flatten the Another World intro VM into a linear playlist.

Runs the VM once on the PC and records the render side-effects in execution
order (set-palette, page select/fill/copy, draw-polygon, blit+hold). The 6502
then just REPLAYS this stream through fill_span / the polygon decoder -- it does
NOT run the VM, threads or branches.

Outputs (out/):
  intro_playlist.bin   the flat command stream (see format below)
  intro_poly.bin       the cinematic polygon data (resource #0x19, raw)
  intro_pal.bin        palette (written by aw_palette.py)

Command stream (little-endian, 6502-friendly):
  0x00 END
  0x01 SETPAL   pal(1)
  0x02 SELPAGE  page(1)                       which buffer to draw into (0..3)
  0x03 FILLPAGE page(1) color(1)
  0x04 COPYPAGE src(1) dst(1)
  0x05 DRAWPOLY off(2) x(2) y(2) zoom(2)      off = byte offset into poly data
  0x06 BLIT     page(1) hold(1)               display page; hold = host-frames
Pages are physical 0..3 (the VM's 0xFE/0xFF are already resolved). Coordinates
are in native 320-wide AW space; the LR (160) build halves x when drawing.
"""
import os, sys, struct, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aw_sim

OUT = os.path.join(os.path.dirname(HERE), 'out')

OPC = {'pal': 0x01, 'sel': 0x02, 'fill': 0x03, 'copy': 0x04,
       'poly': 0x05, 'blit': 0x06, 'text': 0x07, 'snd': 0x08}

# resId -> sfx index (gen_intro_sfx.py); only these sounds are shipped to VRAM.
def _sfx_map():
    p = os.path.join(OUT, 'intro_sfx_map.json')
    if os.path.exists(p):
        return {int(k): v for k, v in json.load(open(p)).items()}
    return {}


def s16(v):
    # Store the low 16 bits verbatim. The VM uses UNSIGNED 16-bit coordinates
    # that the raster sign-extends -- e.g. x=65534 is a sprite at -2, just off
    # the left edge. The old clamp to +-32767 turned that into +32767 (shoved
    # off the RIGHT instead) -> the sprite vanished and the whole frame diverged
    # from the VM (aw_play.py mismatches at 1052/1122; also off-screen 50002 in
    # the elevator descent). The replay sign-extends on read, so raw bits match.
    return struct.pack('<H', v & 0xFFFF)


def serialize(events):
    sfxmap = _sfx_map()
    out = bytearray()
    for e in events:
        k = e[0]
        if k == 'snd':
            idx = sfxmap.get(e[1])     # e = ('snd', resId, freq, vol, ch)
            if idx is None:
                continue               # sound not shipped -> drop the event
            out += bytes((OPC['snd'], idx & 0xFF))
            continue
        out.append(OPC[k])
        if k == 'pal':
            out.append(e[1] & 0xFF)
        elif k == 'sel':
            out.append(e[1] & 0xFF)
        elif k == 'fill':
            out += bytes((e[1] & 0xFF, e[2] & 0xFF))
        elif k == 'copy':
            out += bytes((e[1] & 0xFF, e[2] & 0xFF))
        elif k == 'poly':
            _, off, x, y, zoom = e
            out += struct.pack('<H', off & 0xFFFF) + s16(x) + s16(y) + s16(zoom)
        elif k == 'blit':
            out += bytes((e[1] & 0xFF, e[2] & 0xFF))
        elif k == 'text':
            _, strId, x, y, col = e
            out += struct.pack('<H', strId & 0xFFFF) + bytes((x & 0xFF, y & 0xFF, col & 0xFF))
    out.append(0x00)                         # END
    return bytes(out)


def write_manifest(frames, path):
    """Per-frame polygon manifest: one row per polygon draw. Use it to compare,
    by frame, exactly which polygons SHOULD appear (and their ID=off / position)
    against what the Atari actually renders, to pinpoint dropouts."""
    with open(path, 'w', encoding='ascii') as f:
        f.write('frame,idx,kind,off,x,y,zoom\n')
        for fi, (_pg, _pal, _hold, _draws, dl) in enumerate(frames):
            for n, (kind, off, x, y, zoom) in enumerate(dl):
                f.write(f'{fi},{n},{kind},{off},{x},{y},{zoom}\n')


def main():
    maxf = int(sys.argv[1]) if len(sys.argv) > 1 else 100000
    vm = aw_sim.VM('int')                     # the 6502 raster path
    vm.run(maxf)
    os.makedirs(OUT, exist_ok=True)

    blob = serialize(vm.events)
    open(os.path.join(OUT, 'intro_playlist.bin'), 'wb').write(blob)

    manifest = os.path.join(OUT, 'intro_manifest.csv')
    write_manifest(vm.frames, manifest)

    # polygon data the DRAWPOLY offsets index into (resource #0x19)
    import shutil
    poly_src = os.path.join(OUT, '19.bin')
    if os.path.exists(poly_src):
        shutil.copyfile(poly_src, os.path.join(OUT, 'intro_poly.bin'))

    # stats
    from collections import Counter
    c = Counter(e[0] for e in vm.events)
    print(f'frames (BLIT)  : {c.get("blit",0)}')
    print(f'events total   : {len(vm.events)}')
    for k in ('pal', 'sel', 'fill', 'copy', 'poly', 'blit', 'text', 'snd'):
        print(f'  {k:5}: {c.get(k,0)}')
    print(f'playlist bytes : {len(blob)}  -> out/intro_playlist.bin')
    print(f'poly data      : {os.path.getsize(os.path.join(OUT,"intro_poly.bin"))} '
          f'bytes -> out/intro_poly.bin')
    rows = sum(len(fr[4]) for fr in vm.frames)
    print(f'manifest       : {rows} polygon rows -> out/intro_manifest.csv')


if __name__ == '__main__':
    main()
