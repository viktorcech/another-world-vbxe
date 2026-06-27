#!/usr/bin/env python3
"""
game_pack.py - extract one GAME part's resources to out/ .bin files for the 6502
build (the game's data-prep, the counterpart to aw_palette.py/aw_playlist.py which
prep the intro).

For a part (default 16002 = water) it writes, raw and ready to stream into VRAM:

  out/<name>_pal.bin   32 palettes x 16 cols x RGB (1536 B)  -- VGA half, 4->7 bit,
                        identical format to intro_pal.bin so set_palette just works.
  out/<name>_code.bin  the part bytecode resource          (-> VRAM PLAY_BASE bank).
  out/<name>_v1.bin    video1 (part polygon shapes)        (-> VRAM video1 bank).
  out/<name>_v2.bin    video2 (shared "common" shapes)     (-> VRAM video2 bank).

The 6502 game VM (Phase 1) fetches the bytecode through pl_byte (PLAY_BASE) and the
shapes through poly_fetch (video1 base / video2 base), exactly as the intro does.

Run from the project root:   python tools/game_pack.py            # water
                             python tools/game_pack.py 16003 jail # jail, named
"""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "out")
sys.path.insert(0, HERE)
import aw_pack

# rawgl Resource::_memListParts  {palette, bytecode, video1, video2}  (game_sim order)
MEMLIST_PARTS = {
    16000: (0x14, 0x15, 0x16, 0x00),   # protection
    16001: (0x17, 0x18, 0x19, 0x00),   # intro
    16002: (0x1A, 0x1B, 0x1C, 0x11),   # water  (gameplay start)
    16003: (0x1D, 0x1E, 0x1F, 0x11),   # jail
    16004: (0x20, 0x21, 0x22, 0x11),   # cite
    16005: (0x23, 0x24, 0x25, 0x00),   # arene
    16006: (0x26, 0x27, 0x28, 0x11),   # luxe
    16007: (0x29, 0x2A, 0x2B, 0x11),   # final
    16008: (0x7D, 0x7E, 0x7F, 0x00),   # password
}
DEFAULT_NAMES = {16002: 'water', 16003: 'jail', 16004: 'cite', 16005: 'arene',
                 16006: 'luxe', 16007: 'final', 16001: 'intro2', 16000: 'prot',
                 16008: 'passwd'}

NPAL, NCOL = 32, 16


def to7(c4):                       # 4-bit AW channel -> 7-bit VBXE channel (as aw_palette)
    c8 = (c4 << 4) | c4
    return c8 >> 1


def pal_bytes(data):
    """32x16 RGB from the VGA half (base 0) of a 2048-byte palette resource."""
    out = bytearray()
    for p in range(NPAL):
        o = p * NCOL * 2
        for i in range(NCOL):
            v = struct.unpack_from(">H", data, o + i*2)[0]
            out += bytes((to7((v >> 8) & 0xF), to7((v >> 4) & 0xF), to7(v & 0xF)))
    return bytes(out)


def main():
    part = int(sys.argv[1]) if len(sys.argv) > 1 else 16002
    name = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_NAMES.get(part, f'p{part}')
    if part not in MEMLIST_PARTS:
        sys.exit(f"unknown part {part}")
    pa, co, v1, v2 = MEMLIST_PARTS[part]
    mem = aw_pack.read_memlist()
    os.makedirs(OUT, exist_ok=True)

    pal = aw_pack.load_resource(mem[pa])[0]
    code = aw_pack.load_resource(mem[co])[0]
    vid1 = aw_pack.load_resource(mem[v1])[0]
    vid2 = aw_pack.load_resource(mem[v2])[0] if v2 else b''

    writes = [
        (f"{name}_pal.bin", pal_bytes(pal)),
        (f"{name}_code.bin", code),
        (f"{name}_v1.bin", vid1),
        (f"{name}_v2.bin", vid2),
    ]
    print(f"part {part} ({name}): pal=0x{pa:02X} code=0x{co:02X} v1=0x{v1:02X} v2=0x{v2:02X}")
    for fn, blob in writes:
        if not blob:
            print(f"  (skip {fn}: empty)")
            continue
        open(os.path.join(OUT, fn), "wb").write(blob)
        print(f"  out/{fn:16} = {len(blob):6} bytes")


if __name__ == "__main__":
    main()
