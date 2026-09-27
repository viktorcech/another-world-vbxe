#!/usr/bin/env python3
"""buildstep_join_disk.py - NOT A BUILD. The last step of build.ps1 (the one and
only build): joins the pieces build.ps1 has already made into awgame_full.atr and
verifies the layout. Run alone it just re-joins whatever stale pieces lie around.

awgame_full.atr = ONE bootable disk that plays the intro (with music) and then
chain-loads the full game.

Layout (128-byte sectors, 1-based):
  1 - 3          boot loader                (out/boot.bin, src_game/bootloader.asm)
  4 - A          out/awintro_zx.bin         (the intro ZX02-packed: tools/pack_xex.py)
  GAME_SEC - B   out/awgame_zx.bin          (the game program ZX02-packed: tools/pack_xex.py)
  base - end     game part blob             (out/game_parts_zx.bin, ZX02-packed, addressed by game_atr.inc)

The boot loader loads awintro.xex (from sector 4) and runs it. When the intro
finishes, intro_done (built with -d:GAME_SEC=..) re-enters the boot loader at
$070D with cur_sec = GAME_SEC, so the SAME loader parses awgame.xex and JMPs into
the game. The game then streams its parts from `base` via src_game/game_atr.inc.

GAME_SEC = 4 + intro_sectors, and the game's part table was regenerated with
base = GAME_SEC + game_xex_sectors -- both set up by build.ps1 BEFORE this
script runs. This script only concatenates the pieces and writes the ATR header;
it re-derives GAME_SEC/base from the file sizes and prints the map so the layout
can be eyeballed against the build log.

Run from the project root (via build.ps1):  python tools/buildstep_join_disk.py
"""
import os, re, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
OUT = os.path.join(PROJ, "out")
SECTOR = 128

BOOT = os.path.join(OUT, "boot.bin")                 # 3-sector XEX boot loader
INTRO = os.path.join(PROJ, "awintro.xex")            # intro (with music), plain: the checks below
INTRO_ZX = os.path.join(OUT, "awintro_zx.bin")       # ... packed, what goes onto the disk
GAME = os.path.join(PROJ, "awgame.xex")              # game program, plain: the checks below
GAME_ZX = os.path.join(OUT, "awgame_zx.bin")         # ... packed, what the intro chain loads
BLOB = os.path.join(OUT, "game_parts_zx.bin")        # ZX02-packed part data (make_game_atr.pack_blob)
ATR = os.path.join(PROJ, "awgame_full.atr")          # bootable combined disk, mount on D1:


def secs(n):
    return (n + SECTOR - 1) // SECTOR


def main():
    for p in (BOOT, INTRO, INTRO_ZX, GAME, GAME_ZX, BLOB):
        if not os.path.exists(p):
            sys.exit(f"missing {p} -- run build.ps1 (it builds every piece first)")

    boot = open(BOOT, "rb").read()
    if len(boot) > 3 * SECTOR:
        sys.exit(f"boot.bin too big ({len(boot)} > {3*SECTOR})")
    boot = boot.ljust(3 * SECTOR, b"\x00")

    intro = open(INTRO, "rb").read()                 # plain: chain / VBXE checks
    intro_disk = open(INTRO_ZX, "rb").read()         # packed + padded by pack_xex
    intro_disk = intro_disk.ljust(secs(len(intro_disk)) * SECTOR, b"\x00")
    intro_sectors = len(intro_disk) // SECTOR

    game = open(GAME, "rb").read()                   # plain: VBXE checks
    game_disk = open(GAME_ZX, "rb").read()           # packed + padded by pack_xex
    game_disk = game_disk.ljust(secs(len(game_disk)) * SECTOR, b"\x00")
    game_sectors = len(game_disk) // SECTOR

    blob = open(BLOB, "rb").read()
    if len(blob) % SECTOR:
        sys.exit(f"blob not sector-aligned ({len(blob)} B) -- rebuild via make_game_atr")

    game_sec = 4 + intro_sectors            # 1-based ATR sector of awgame.xex (== GAME_SEC)
    base = game_sec + game_sectors          # 1-based ATR sector of the part blob

    # --- HARD layout verification (was only a printed reminder) ---------------------
    # Both constants are baked into code long before this script runs, so a mismatch
    # produces a disk that boots and then quietly reads the WRONG sectors:
    #   * intro_done jumps into the boot loader with cur_sec = GAME_SEC. Wrong -> the
    #     loader parses garbage as an XEX and jumps into the weeds.
    #   * the game's part table is GAME_BLOB_BASE + relative. Wrong -> the VM streams
    #     someone else's bytes as its bytecode and runs off into the weeds.
    inc = open(os.path.join(PROJ, "src_game", "game_atr.inc"), encoding="latin-1").read()
    m = re.search(r"^GAME_BLOB_BASE\s*=\s*(\d+)", inc, re.M)
    if not m:
        sys.exit("src_game/game_atr.inc has no GAME_BLOB_BASE -- regenerate it with "
                 f"make_game_atr.py --xex-start {game_sec} --no-atr")
    baked_base = int(m.group(1))
    if baked_base != base:
        sys.exit(f"LAYOUT MISMATCH: the part blob lands at sector {base}, but the game "
                 f"was built for {baked_base}.\n  fix: python tools/make_game_atr.py "
                 f"--xex-start {game_sec} --no-atr   then re-assemble awgame.xex")
    # --- the intro -> loader chain, verified against BOTH binaries ------------------
    # intro_done (src/aw_exit.asm) re-enters the resident boot loader by poking its
    # variables and jumping at its entry. Nothing links the two, so the addresses in
    # aw_exit.asm are a hand-written copy of bootloader.asm's layout -- and they were
    # wrong for weeks: they assumed the variables sit right behind the boot header,
    # while the Atari boot protocol needs load_address+6 ($0706) to be CODE, so the
    # loader keeps its variables at the END. The chain therefore overwrote
    # `lda #$00 / sta SDMCTL` and jumped into the middle of `sta $D400`.
    # Rather than trust either side, DECODE the emitted chain and check every address
    # against the loader image that is about to go onto sectors 1-3.
    lead = bytes((0x78, 0xA9, 0x00, 0x8D, 0x40, 0xD6, 0x8D, 0x5E, 0xD6, 0x8D, 0x5D, 0xD6))
    at = intro.find(lead)
    if at < 0 or intro.count(lead) != 1:
        sys.exit("CHAIN NOT FOUND in awintro.xex -- was it assembled with "
                 f"-d:GAME_SEC={game_sec}? (src/aw_exit.asm intro_done)")
    c = at + len(lead)

    def st(i):
        """decode `lda #imm / sta abs` at offset i -> (imm, addr)"""
        if intro[i] != 0xA9 or intro[i + 2] != 0x8D:
            sys.exit(f"CHAIN DECODE FAILED at intro offset {i} -- intro_done changed "
                     "shape; update tools/buildstep_join_disk.py")
        return intro[i + 1], intro[i + 3] | (intro[i + 4] << 8)

    lo_v, lo_a = st(c)            # cur_sec low
    hi_v, hi_a = st(c + 5)        # cur_sec high
    bp_v, bp_a = st(c + 10)       # buf_pos
    if intro[c + 15] != 0x58 or intro[c + 16] != 0x4C:
        sys.exit("CHAIN DECODE FAILED: expected `cli / jmp boot_init` after the pokes")
    chain_tgt = intro[c + 17] | (intro[c + 18] << 8)

    boot_init = boot[4] | (boot[5] << 8)                 # loader header: entry address
    baked_sec = lo_v | (hi_v << 8)
    if baked_sec != game_sec:
        sys.exit(f"LAYOUT MISMATCH: the intro chains to sector {baked_sec}, but "
                 f"awgame.xex starts at {game_sec}.\n"
                 f"  fix: mads src/awvbxe.asm -d:GAME_SEC={game_sec} -o:awintro.xex")
    if chain_tgt != boot_init:
        sys.exit(f"CHAIN MISMATCH: the intro jumps to ${chain_tgt:04X} but the loader's "
                 f"entry is ${boot_init:04X}.\n"
                 f"  fix BOOT_INIT in src/aw_exit.asm (or re-assemble the loader)")
    if hi_a != lo_a + 1:
        sys.exit(f"CHAIN MISMATCH: cur_sec halves written to ${lo_a:04X}/${hi_a:04X}")
    # the pokes must land on the loader's INITIALISED VARIABLES, i.e. the bytes there
    # must still hold bootloader.asm's defaults (cur_sec=4, buf_pos=128). If they hold
    # anything else we are writing over code.
    def img(a):
        i = a - 0x0700
        if not (0 <= i < len(boot)):
            sys.exit(f"CHAIN MISMATCH: ${a:04X} is outside the 3-sector loader image")
        return boot[i]
    if (img(lo_a) | (img(hi_a) << 8)) != 4:
        sys.exit(f"CHAIN MISMATCH: cur_sec poked at ${lo_a:04X}, but the loader has "
                 f"${img(lo_a):02X} ${img(hi_a):02X} there (expected 04 00) -- that is "
                 f"loader CODE, not the variable.\n"
                 f"  fix BOOT_CURSEC in src/aw_exit.asm")
    if img(bp_a) != 128 or bp_v != 128:
        sys.exit(f"CHAIN MISMATCH: buf_pos poked at ${bp_a:04X}, but the loader has "
                 f"${img(bp_a):02X} there (expected 80) -- that is loader CODE.\n"
                 f"  fix BOOT_BUFPOS in src/aw_exit.asm")
    print(f"chain: cur_sec ${lo_a:04X}<-{game_sec}  buf_pos ${bp_a:04X}<-128  "
          f"jmp ${chain_tgt:04X}   (verified against out/boot.bin)")

    # --- the VBXE fail-fast check: ONE test, THREE binaries -------------------------
    # The boot loader carries its own copy of detect_vbxe's $D600 FX-core test
    # (bootloader.asm vbxe_check): it must fail ~1 s after power-on, long before
    # awintro.xex (which holds the real detect_vbxe, src/aw_vbxe.asm) is in RAM, so
    # the CODE cannot be shared. What can silently drift is the acceptance criteria.
    # Read the constants out of the loader's emitted bytes and require the intro and
    # the game to compare against the exact same values: whoever edits one copy
    # without the other stops the build right here.
    m1 = re.search(rb"\xAD\x40\xD6\xC9(.)", boot, re.S)          # lda $D640 / cmp #core
    m2 = re.search(rb"\xAD\x41\xD6\x29(.)\xC9(.)", boot, re.S)   # lda $D641 / and #mask / cmp #minrev
    if not (m1 and m2):
        sys.exit("VBXE CHECK MISSING: out/boot.bin has no CORE_VERSION/MINOR_REVISION "
                 "test -- bootloader.asm lost vbxe_check")
    for name, img in (("awintro.xex", intro), ("awgame.xex", game)):
        if m1.group(0) not in img or m2.group(0) not in img:
            sys.exit(f"VBXE CHECK DRIFT: {name}'s detect_vbxe does not test the same "
                     f"core/revision as the boot loader's vbxe_check.\n"
                     f"  keep src/aw_vbxe.asm and src_game/bootloader.asm in sync")
    # ... and ONE canonical message text. The loader prints screen codes (dta d''),
    # the xexes ATASCII via CIO (dta c''), so derive both encodings from the one
    # literal here and require every binary to carry it.
    MSG = b"VBXE NOT DETECTED!"
    scr = bytes(c - 0x20 for c in MSG)               # ATASCII $20-$5F -> screen code
    if scr not in boot:
        sys.exit("VBXE MESSAGE DRIFT: the boot loader does not print "
                 "'VBXE NOT DETECTED!' (src_game/bootloader.asm vbxe_msg)")
    for name, img in (("awintro.xex", intro), ("awgame.xex", game)):
        if MSG not in img:
            sys.exit(f"VBXE MESSAGE DRIFT: {name} does not carry 'VBXE NOT DETECTED!' "
                     f"(src/aw_exit.asm nv_msg / src_game/awgame.asm nv_msg)")
    print(f"vbxe : fail-fast check (core=${m1.group(1)[0]:02X}, "
          f"rev&${m2.group(1)[0]:02X}>=${m2.group(2)[0]:02X}) + one message text, "
          f"agree across boot/intro/game")

    disk = boot + intro_disk + game_disk + blob
    total_sec = len(disk) // SECTOR
    para = (total_sec * SECTOR) // 16
    hdr = bytearray(16)
    struct.pack_into("<H", hdr, 0, 0x0296)           # ATR magic
    struct.pack_into("<H", hdr, 2, para & 0xFFFF)    # size in 16-byte paragraphs (lo)
    struct.pack_into("<H", hdr, 4, SECTOR)
    hdr[6] = (para >> 16) & 0xFF                     # paragraphs (hi) -- large ATR
    open(ATR, "wb").write(hdr + disk)

    print(f"boot : 1-3")
    print(f"intro: 4-{3+intro_sectors}            (awintro_zx.bin, {len(intro_disk)//1024} KB)")
    print(f"game : {game_sec}-{game_sec+game_sectors-1}   GAME_SEC={game_sec}  (awgame_zx.bin, {len(game_disk)//1024} KB)")
    print(f"parts: {base}-{total_sec}   base={base}         (out/game_parts_zx.bin, {len(blob)//1024} KB)")
    print(f"awgame_full.atr : {total_sec} sectors ({len(disk)//1024} KB, bootable)")
    print(f"    -> the intro's -d:GAME_SEC must == {game_sec}; game_atr.inc base must == {base}")


if __name__ == "__main__":
    main()
