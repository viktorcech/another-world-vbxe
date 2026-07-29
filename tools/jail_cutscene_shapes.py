#!/usr/bin/env python3
"""jail_cutscene_shapes.py - static analysis of the JAIL (16003) window-cutscene
polygon shapes ($8894 panorama), WITHOUT running the VM (the cutscene is gameplay-
gated and can't be reached in any simulator).

Decodes each draw_spr shape (by byte offset, from the disassembly) directly out of the
two poly banks and measures, per shape:
    bank it decodes cleanly in (video1=poly1 / video2=poly2)
    max single-polygon vertex count   (vs the 6502 vertex buffer = 64)
    max hierarchy depth                (vs do_hier recursion limit, old jail max = 3)
    polygon / hier-node counts
Read order mirrors tools/aw_sim.py PolyData._fill / _hier exactly.

Run from tools/:  python jail_cutscene_shapes.py
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
DATA = os.path.join(PROJ, 'out', 'jaildata')
poly1 = open(os.path.join(DATA, 'poly1.bin'), 'rb').read()   # video1 (jail-specific)
poly2 = open(os.path.join(DATA, 'poly2.bin'), 'rb').read()   # video2 (shared "common")


def analyze(d, off, depth=0, st=None, seen=None):
    if st is None:
        st = dict(maxverts=0, maxverts_off=0, maxdepth=0, npoly=0, nhier=0, ok=True)
    if seen is None:
        seen = set()
    if off in seen or off < 0 or off >= len(d):
        return st
    seen.add(off)
    st['maxdepth'] = max(st['maxdepth'], depth)
    i = d[off]; off += 1
    if i >= 0xC0:                       # single fill polygon
        if off + 3 > len(d): st['ok'] = False; return st
        off += 2                        # bbw, bbh
        n = d[off]; off += 1
        st['npoly'] += 1
        if n > st['maxverts']:
            st['maxverts'] = n; st['maxverts_off'] = off - 4
        off += 2 * n
    elif (i & 0x3F) == 2:               # hierarchy of children
        if off + 3 > len(d): st['ok'] = False; return st
        off += 2                        # bx, by
        childs = d[off]; off += 1
        st['nhier'] += 1
        for _ in range(childs + 1):
            if off + 4 > len(d): st['ok'] = False; return st
            word = (d[off] << 8) | d[off + 1]; off += 2
            off += 2                    # cx, cy
            if word & 0x8000:
                off += 2                # child colour
            analyze(d, (word & 0x7FFF) * 2, depth + 1, st, seen)
    else:
        st['ok'] = False
    return st


# unique draw_spr offsets used by the window cutscene threads (from disasm.py 16003)
OFFS = [17548, 17704, 17856, 18000, 18132, 18260, 18396, 18524, 18648, 18772, 18864,
        18948, 19052, 19148, 19276, 19396, 19520, 22468, 22648, 22852, 23048, 23212,
        23372, 896, 2124, 2260, 2812, 2854, 2926, 2998, 3338]


def main():
    print(f"poly1(video1)={len(poly1)}B  poly2(video2)={len(poly2)}B   (vertex buffer = 64)\n")
    print(f"{'off':>6}  {'bank':<11} {'verts':>5} {'depth':>5} {'npoly':>5} {'nhier':>5}  note")
    for o in sorted(set(OFFS)):
        # pick the bank in which the shape decodes cleanly
        chosen = None
        for name, d in (('video1/poly1', poly1), ('video2/poly2', poly2)):
            if o < len(d):
                st = analyze(d, o)
                if st['ok'] and (chosen is None):
                    chosen = (name, st)
        if chosen is None:
            print(f"{o:>6}  {'?':<11} decode failed in both banks")
            continue
        name, st = chosen
        note = ''
        if st['maxverts'] > 64:
            note = f'*** >64 VERTS -> overflow @off {st["maxverts_off"]}'
        elif st['maxverts'] > 22:
            note = '>22 verts (above old jail survey max)'
        print(f"{o:>6}  {name:<11} {st['maxverts']:>5} {st['maxdepth']:>5} "
              f"{st['npoly']:>5} {st['nhier']:>5}  {note}")


if __name__ == '__main__':
    main()
