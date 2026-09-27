#!/usr/bin/env python3
"""make_gr7_atr.py - build gr7/awgr7_full.atr : ONE bootable disk for a plain
320K Atari XL/XE that plays the GR.7 intro (awgr7.xex) and then chain-loads
the GR.7 game (awgr7g.xex) -- the counterpart of tools/buildstep_join_disk.py.

Layout (128-byte sectors, 1-based):
  1 - 3          boot loader                (gr7/out/gr7boot.bin, gr7_boot.asm)
  4 - A          awgr7.xex                  (intro; its data streams in via INI
                                            bank-select stubs, like the VBXE one)
  GAME_SEC - B   awgr7g.xex                 (the game: two resident parts)

The loader loads awgr7.xex (from sector 4) and runs it. When the intro ends
(or ESC), intro_done re-enters the resident loader at BOOT_INIT with cur_sec =
GAME_SEC, so the SAME loader parses awgr7g.xex and JMPs into the game. There
is no part blob: the game carries both its parts inside the xex.

GAME_SEC = 4 + intro_sectors is baked into the intro (mads -d:GAME_SEC=..) by
build_gr7.ps1 BEFORE this script runs; this script re-derives it from the file
sizes and HARD-verifies the chain against the loader image (the pokes must
land on the loader's initialised variables, the jump on its entry).

Run from the project root (via gr7/build_gr7.ps1):  python gr7/tools/make_gr7_atr.py
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GR7 = os.path.dirname(HERE)
OUT = os.path.join(GR7, 'out')
SECTOR = 128

BOOT = os.path.join(OUT, 'gr7boot.bin')
BOOTLST = os.path.join(OUT, 'gr7boot.lst')
INTRO = os.path.join(GR7, 'awgr7.xex')
GAME = os.path.join(GR7, 'awgr7g.xex')
ATR = os.path.join(GR7, 'awgr7_full.atr')
BLOB = os.path.join(os.path.dirname(GR7), 'out', 'game_parts.bin')   # the VBXE disk's blob
ATRINC = os.path.join(OUT, 'gp_atr.inc')


def secs(n):
    return (n + SECTOR - 1) // SECTOR


def labels(lst):
    out = {}
    for ln in open(lst, encoding='latin-1'):
        m = re.match(r'^\s*\d+\s+([0-9A-F]{4})\s+.*?\t(\w+)\s', ln)
        if m:
            out.setdefault(m.group(2), int(m.group(1), 16))
    return out


def main():
    for p in (BOOT, BOOTLST, INTRO, GAME):
        if not os.path.exists(p):
            sys.exit(f'missing {p} -- run gr7/build_gr7.ps1 (it builds every piece first)')
    boot = open(BOOT, 'rb').read()
    if len(boot) > 3 * SECTOR:
        sys.exit(f'gr7boot.bin too big ({len(boot)} > {3 * SECTOR})')
    boot = boot.ljust(3 * SECTOR, b'\x00')
    intro = open(INTRO, 'rb').read()
    intro = intro.ljust(secs(len(intro)) * SECTOR, b'\x00')
    intro_sectors = len(intro) // SECTOR
    game = open(GAME, 'rb').read()
    game = game.ljust(secs(len(game)) * SECTOR, b'\x00')
    game_sectors = len(game) // SECTOR
    game_sec = 4 + intro_sectors

    # --- the intro -> loader chain, verified against the loader image ---------
    lab = labels(BOOTLST)
    cursec, bufpos, entry = lab['cur_sec'], lab['buf_pos'], lab['boot_init']
    chain = bytes([0xA9, game_sec & 0xFF, 0x8D, cursec & 0xFF, cursec >> 8,
                   0xA9, game_sec >> 8, 0x8D, (cursec + 1) & 0xFF, (cursec + 1) >> 8,
                   0xA9, 128, 0x8D, bufpos & 0xFF, bufpos >> 8,
                   0x58, 0x4C, entry & 0xFF, entry >> 8])
    if intro.count(chain) != 1:
        sys.exit(f'CHAIN NOT FOUND in awgr7.xex -- was it assembled with '
                 f'-d:GAME_SEC={game_sec} and the current gr7/out/gr7boot.inc? '
                 f'(gr7_main.asm intro_done)')
    img = lambda a: boot[a - 0x0700]
    if (img(cursec) | (img(cursec + 1) << 8)) != 4 or img(bufpos) != 128:
        sys.exit('CHAIN MISMATCH: the loader image has no cur_sec=4 / buf_pos=128 '
                 'at the addresses gr7boot.inc names -- loader CODE would be poked')
    if boot[4] | (boot[5] << 8) != entry:
        sys.exit('CHAIN MISMATCH: the loader header entry != BOOT_INIT')
    print(f'chain: cur_sec ${cursec:04X}<-{game_sec}  buf_pos ${bufpos:04X}<-128  '
          f'jmp ${entry:04X}   (verified against gr7/out/gr7boot.bin)')
    if b'NEED 320K RAM' not in bytes(c + 0x20 for c in boot if c < 0x60) and \
            bytes(c - 0x20 for c in b'NEED 320K RAM') not in boot:
        sys.exit('the loader lost its 320K check message')

    # --- the part blob (streamed by gr7g_loader.asm) and its baked base ---------
    blob = open(BLOB, 'rb').read()
    if len(blob) % SECTOR:
        sys.exit(f'blob not sector-aligned ({len(blob)} B)')
    base = game_sec + game_sectors
    m = re.search(r'^GP_BLOB_BASE\s*=\s*(\d+)', open(ATRINC, encoding='latin-1').read(), re.M)
    if not m or int(m.group(1)) != base:
        sys.exit(f'LAYOUT MISMATCH: the blob lands at sector {base}, the game was built for '
                 f'{m.group(1) if m else "?"} -- rerun gen_gr7_game_data.py --base {base} + mads')
    print(f'blob : {base}-{base + len(blob) // SECTOR - 1}   (out/game_parts.bin, '
          f'{len(blob) // 1024} KB, base verified against gp_atr.inc)')
    disk = boot + intro + game + blob
    total_sec = len(disk) // SECTOR
    para = (total_sec * SECTOR) // 16
    hdr = bytearray(16)
    struct.pack_into('<H', hdr, 0, 0x0296)
    struct.pack_into('<H', hdr, 2, para & 0xFFFF)
    struct.pack_into('<H', hdr, 4, SECTOR)
    hdr[6] = (para >> 16) & 0xFF
    open(ATR, 'wb').write(hdr + disk)
    print('boot : 1-3   (320K check, then awgr7.xex)')
    print(f'intro: 4-{3 + intro_sectors}            (awgr7.xex, {len(intro) // 1024} KB)')
    print(f'game : {game_sec}-{game_sec + game_sectors - 1}   GAME_SEC={game_sec}  '
          f'(awgr7g.xex, {len(game) // 1024} KB)')
    print(f'gr7/awgr7_full.atr : {total_sec} sectors ({len(disk) // 1024} KB, bootable, '
          f'Altirra: 320K Rambo, no VBXE needed)')


if __name__ == '__main__':
    main()
