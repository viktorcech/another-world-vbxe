#!/usr/bin/env python3
"""Another World (Atari+VBXE GAME build) - guard XEX segments against landing in
reserved RAM, and against overlapping each other.

The old tools/check_layout.py only loosely checked "code top vs $4000" and MISSED
two real overflows that each cost a debugging session:
  * game_text.asm grew past $4000 -> op_drawstring executed the MEMAC-B VRAM
    window = KIL @ $4037.
  * game_sound.asm then grew past $4000 -> the Timer-1 IRQ executed the window
    = KIL @ $41E0 (+ black screen).
This parses the *actual* segmented XEX and fails the build if any segment overlaps
a reserved range, so the boundary is caught at build time (mads has no such check
and separate `org`s don't trip its overflow guard).

Reserved ranges (see the VRAM/RAM map in src/aw_equates.inc + awgame.asm):
  $4000-$7FFF  MEMAC-B window  -- CPU reads/writes here hit VBXE VRAM, NOT RAM, so
                                  any code/data the XEX puts here is lost and
                                  EXECUTING it runs VRAM bytes -> KIL.
  $8000-$8FFF  MEMAC-A window  -- same (XDL/BCB control bank).
  $B000-$B3FF  VM state        -- var_lo/var_hi/tpc/tpause/treq/vstk/dk_* (declared
                                  by equ, no XEX bytes). A relocated $B400 module
                                  growing DOWN, or any segment landing here, would
                                  clobber the VM. (Relocated code lives $B400-$BFFF.)
  $C000-$FFFF  Atari OS ROM    -- writes land nowhere; a jsr here reads OS code -> KIL.

$A000-$BFFF is NOT reserved: the `ini disable_basic` segment turns BASIC ROM off
during load, so that region is RAM both at load and run time (the relocated
game_diskio/game_sound/game_text modules live at $B400).

Usage:  python check_xex.py awgame.xex
"""
import struct
import sys

# (lo, hi, description) - inclusive byte ranges no XEX segment may touch.
RESERVED = [
    (0x4000, 0x7FFF, "MEMAC-B window (CPU access hits VBXE VRAM, not RAM)"),
    (0x8000, 0x8FFF, "MEMAC-A window (XDL/BCB control bank)"),
    (0xB000, 0xB3FF, "VM state (var/tpc/tpause/treq/vstk/dk_*)"),
    (0xC000, 0xFFFF, "Atari OS ROM"),
]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "awgame.xex"
    data = open(path, "rb").read()
    i = 0
    if data[0:2] == b"\xff\xff":
        i = 2
    bad = []
    segs = []                               # (start, end) of every real data segment
    while i + 4 <= len(data):
        start, end = struct.unpack_from("<HH", data, i)
        if start == 0xFFFF:                 # optional segment-header marker
            i += 2
            continue
        i += 4
        seg_len = (end - start + 1) & 0xFFFF
        # INIT ($02E2) / RUN ($02E0) are tiny 2-byte page-2 vector segments -- skip
        # them in the overlap checks (they legitimately sit in the OS vector page).
        if start not in (0x02E0, 0x02E2):
            segs.append((start, end))
            for lo, hi, why in RESERVED:
                if not (end < lo or start > hi):
                    bad.append((start, end, lo, hi, why))
        i += seg_len

    # Inter-segment overlap: two segments targeting the same byte -> later load wins.
    overlaps = []
    for a in range(len(segs)):
        sa, ea = segs[a]
        for b in range(a + 1, len(segs)):
            sb, eb = segs[b]
            if sa <= eb and sb <= ea:
                overlaps.append((sa, ea, sb, eb, max(sa, sb), min(ea, eb)))

    if bad or overlaps:
        print("XEX BOUNDARY CHECK FAILED:")
        for s, e, lo, hi, why in bad:
            print(f"  segment ${s:04X}-${e:04X} overlaps reserved "
                  f"${lo:04X}-${hi:04X} ({why}) -> lost/clobbered/KIL at runtime.")
        for sa, ea, sb, eb, lo, hi in overlaps:
            print(f"  segment ${sa:04X}-${ea:04X} overlaps segment "
                  f"${sb:04X}-${eb:04X} on ${lo:04X}-${hi:04X} ({hi - lo + 1} B).")
        sys.exit(1)

    print(f"  check_xex: {len(segs)} segments, none in reserved RAM:")
    for s, e in sorted(segs):
        print(f"    ${s:04X}-${e:04X}  ({e - s + 1} B)")
    print("  ok  XEX boundary check passed.")


if __name__ == "__main__":
    main()
