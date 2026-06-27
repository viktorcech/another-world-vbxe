#!/usr/bin/env python3
"""
aw_palette.py - convert an Another World palette resource to VBXE 7-bit colour.

AW stores 32 palettes, 16 colours each, 2 bytes/colour, big-endian 0x0RGB
(4 bits per channel). The 2048-byte resource holds two 1024-byte halves
(EGA-ish + VGA); we take the VGA half. VBXE channels are 7-bit, so a 4-bit
component c expands as ((c<<4)|c) >> 1  (0..15 -> 0..127).

Output: out/intro_pal.bin = 32 palettes * 16 colours * (R,G,B) = 1536 bytes.
The 6502 palette-set routine writes one palette's 16 colours into VBXE
indices 0..15 (palette #1) before the scene that uses it.
"""
import os, struct, sys

OUT = os.path.join(os.path.dirname(__file__), "..", "out")
NPAL, NCOL = 32, 16


def to7(c4):                       # 4-bit AW channel -> 7-bit VBXE channel
    c8 = (c4 << 4) | c4
    return c8 >> 1


def decode(data, base):
    pals = []
    for p in range(NPAL):
        cols = []
        o = base + p * NCOL * 2
        for i in range(NCOL):
            v = struct.unpack_from(">H", data, o + i*2)[0]
            r = to7((v >> 8) & 0xF)
            g = to7((v >> 4) & 0xF)
            b = to7(v & 0xF)
            cols.append((r, g, b))
        pals.append(cols)
    return pals


def main():
    data = open(os.path.join(OUT, "17.bin"), "rb").read()   # intro palette (#0x17)
    # The resource holds VGA (first 1024 bytes) then EGA (second 1024). The VGA
    # half is the clean 0x0RGB format the game uses (rawgl reads _segVideoPal+
    # palNum*32 from offset 0); every VGA colour has high nibble 0. The EGA half
    # has garbage high nibbles and darker values -- taking it (the old bug) made
    # the whole intro too dark and the elevator-descent line invisible.
    base = 0
    pals = decode(data, base)

    out = bytearray()
    for cols in pals:
        for (r, g, b) in cols:
            out += bytes((r, g, b))
    open(os.path.join(OUT, "intro_pal.bin"), "wb").write(out)

    print(f"intro_pal.bin: {NPAL} palettes x {NCOL} cols (VGA half @{base}) "
          f"= {len(out)} bytes")
    for p in (0, 1, 2):
        sw = " ".join("%02x%02x%02x" % c for c in pals[p][:8])
        print(f"  pal {p:2}: {sw} ...")


if __name__ == "__main__":
    main()
