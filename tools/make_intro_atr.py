#!/usr/bin/env python3
"""make_intro_atr.py - build the bootable INTRO disk (awintro.atr).

Layout: the 3-sector XEX boot loader (out/boot.bin, src_game/bootloader.asm)
on sectors 1-3, then awintro.xex parsed straight from raw sectors starting at
sector 4 (the loader supports the INIT segments the intro uses to stream its
data into VBXE VRAM through the MEMAC-B window). The intro is self-contained
-- everything lives inside the XEX -- so unlike awgame.atr the disk carries no
extra resource blob.

Run from the project root:  python tools/make_intro_atr.py
"""
import os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
SECTOR = 128

BOOT = os.path.join(PROJ, "out", "boot.bin")
XEX = os.path.join(PROJ, "awintro.xex")
ATR = os.path.join(PROJ, "awintro.atr")


def main():
    if not os.path.exists(BOOT):
        sys.exit(f"{BOOT} missing -- assemble src_game/bootloader.asm first")
    if not os.path.exists(XEX):
        sys.exit(f"{XEX} missing -- build awintro.xex first (mads src/awvbxe.asm)")

    boot = open(BOOT, "rb").read()
    if len(boot) > 3 * SECTOR:
        sys.exit(f"boot.bin too big ({len(boot)} > {3*SECTOR})")
    boot = boot.ljust(3 * SECTOR, b"\x00")

    xex = open(XEX, "rb").read()
    pad = (-len(xex)) % SECTOR
    xex_padded = xex + b"\x00" * pad
    xex_sectors = len(xex_padded) // SECTOR

    disk = boot + xex_padded
    total_sec = len(disk) // SECTOR
    para = (total_sec * SECTOR) // 16
    hdr = bytearray(16)
    struct.pack_into("<H", hdr, 0, 0x0296)          # ATR magic
    struct.pack_into("<H", hdr, 2, para & 0xFFFF)   # size in 16-byte paragraphs
    struct.pack_into("<H", hdr, 4, SECTOR)
    hdr[6] = (para >> 16) & 0xFF
    open(ATR, "wb").write(hdr + disk)

    print(f"boot: 1-3   xex: 4-{3+xex_sectors} ({len(xex)} B)")
    print(f"awintro.atr : {total_sec} sectors ({len(disk)//1024} KB, bootable)")


if __name__ == "__main__":
    main()
