#!/usr/bin/env python3
"""hoist_setpal.py - move each SETPAL to the front of its frame.

Another World's SETPAL belongs to the frame that is about to be blitted, and the
VBXE build can honour that by simply reloading the palette at the flip: its page
stores AW colour INDICES and the hardware resolves them at display time.

A GRAPHICS 7 page stores 2-bit CODES, so the index->code mapping has to be known
while drawing. Anything the playlist does before the SETPAL -- FILLPAGE, COPYPAGE,
even a DRAWPOLY -- is therefore coded through the previous scene's palette, and a
copy cannot be undone afterwards (the code has lost which index produced it).

The playlist is a flat, linear stream, so the fix is free: hoist the SETPAL of each
frame to the start of that frame. Nothing else is reordered.

    python gr7/hoist_setpal.py   ->  gr7/out/intro_playlist_gr7.bin
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
SRC = os.path.join(PROJ, 'out', 'intro_playlist.bin')
DST = os.path.join(HERE, 'out', 'intro_playlist_gr7.bin')

OPERANDS = {0: 0, 1: 1, 2: 1, 3: 2, 4: 2, 5: 8, 6: 2, 7: 5, 8: 2, 9: 0}
SETPAL, BLIT, END = 1, 6, 0


def parse(d):
    out, i = [], 0
    while i < len(d):
        op = d[i]
        n = OPERANDS.get(op)
        if n is None:                       # unknown opcode: keep the byte as-is
            out.append((op, b'')); i += 1; continue
        out.append((op, d[i+1:i+1+n])); i += 1 + n
        if op == END:
            break
    return out, d[i:]


def main():
    d = open(SRC, 'rb').read()
    ops, tail = parse(d)

    frames, cur = [], []
    for op, a in ops:
        cur.append((op, a))
        if op == BLIT:
            frames.append(cur); cur = []
    if cur:
        frames.append(cur)

    moved = 0
    out = bytearray()
    for fr in frames:
        pals = [i for i, (op, a) in enumerate(fr) if op == SETPAL]
        if pals:
            last = fr[pals[-1]]                      # the palette this frame ends on
            rest = [x for i, x in enumerate(fr) if i not in set(pals)]
            if pals[-1] != 0 or len(pals) > 1:
                moved += 1
            fr = [last] + rest
        for op, a in fr:
            out.append(op); out += a
    out += tail
    # Dropping a redundant second SETPAL in one frame shortens the stream. Pad back
    # to the original length so the bank chunking in gr7_main.asm stays put -- the
    # padding sits past the END opcode and is never read.
    if len(out) < len(d):
        out += bytes(len(d) - len(out))

    os.makedirs(os.path.dirname(DST), exist_ok=True)
    open(DST, 'wb').write(bytes(out))
    print(f'{len(frames)} snimok, SETPAL predsunuty v {moved} z nich')
    print(f'{len(d)} B -> {len(out)} B   -> {os.path.relpath(DST, PROJ)}')
    if len(out) != len(d):
        print('POZOR: dlzka sa zmenila, to by nemala')
        sys.exit(1)


if __name__ == '__main__':
    main()
