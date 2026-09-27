#!/usr/bin/env python3
"""verify_packed_load.py - a packed xex on the disk must load to EXACTLY what its
plain xex loads. Runs the real boot loader (out/boot.bin) on the 6502 core of
_bench_frame.Machine (MEMAC-B + VRAM modelled, SIOV served from a disk image),
each load twice -- plain xex vs the packed file (stage 2 = src_game/xex_unpack.asm):
  intro: the cold boot, loader entered at $0706 with cur_sec = 4
  game : the intro->game chain as intro_done makes it -- dark screen, the loader
         re-entered with cur_sec = GAME_SEC, buf_pos = 128
and stops each at the jump to RUN ($2000). Then compares all RAM outside the
loaders' own areas, all 512 KB of VRAM and the MEMAC-B register, and prints the
sectors read and the CPU time of both loads.

Run from the project root after build.ps1:   python tools/verify_packed_load.py
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _bench_frame as B                                          # noqa: E402

OUT = os.path.join(PROJ, "out")
SECTOR = 128
RUN = 0x2000
# not compared: zp + stack, page 3 (DCB), stage 2 $0480-$05FF, loader $0700-$08FF,
# the percent counter (both runs share one PCT_STEP, sized for the packed intro)
SKIP = [(0x0000, 0x0200), (0x0300, 0x0400), (0x0480, 0x0600), (0x0700, 0x0900),
        (0x9C00, 0x9C20)]                           # + the LOADING nn% text (SAVMSC below)


class Done(Exception):
    pass


def load(disk, boot_bin, lab, first_sec, run_jmp):
    """disk = sectors 1.. (boot included); the loader starts at first_sec. run_jmp =
    the address of the `jmp` that leaves for RUN; the caller has already turned its
    opcode into a trap byte in disk/boot_bin (INITs through it are emulated)."""
    mc = B.Machine(os.path.join(PROJ, "awgame.xex"), os.path.join(PROJ, "awgame_full.atr"),
                   os.path.join(OUT, "awgame.lst"))
    mc.atr = bytes(16) + bytes(disk) + bytes(-len(disk) % SECTOR)
    mc.m[0:0xC000] = bytes(0xC000)                  # cold RAM
    mc.m[0x0700:0x0700 + len(boot_bin)] = boot_bin

    def trap(pc, _o=B.Machine.trap):
        if pc == run_jmp:
            tgt = mc.m[pc + 1] | (mc.m[pc + 2] << 8)
            if tgt == RUN:
                raise Done
            mc.pc = tgt                             # an INIT through the same jmp
            return
        return _o(mc, pc)
    mc.trap = trap
    mc.m[0x58], mc.m[0x59] = 0x00, 0x9C             # SAVMSC: any RAM
    if first_sec == 4:
        mc.m[0x22F] = 0x22                          # cold boot: screen on, draws %
    else:                                           # intro_done: dark, pokes the loader
        mc.m[0x22F] = 0
        mc.m[lab["CUR_SEC"]], mc.m[lab["CUR_SEC"] + 1] = first_sec & 0xFF, first_sec >> 8
        mc.m[lab["BUF_POS"]] = 128
    mc.pc, mc.s, mc.i = 0x0706, 0xFD, 1
    mc.sio_sectors = 0
    try:
        mc.run_until(B.FRAME * 60000)
    except Done:
        return mc
    sys.exit(f"never reached RUN (pc ${mc.pc:04X})")


def compare(what, mp, mz):
    bad = 0
    ram_p, ram_z = bytes(mp.m[:0xC000]), bytes(mz.m[:0xC000])
    lo = 0
    for s, e in SKIP + [(0xC000, 0xC000)]:
        if ram_p[lo:s] != ram_z[lo:s]:
            i = next(i for i in range(lo, s) if ram_p[i] != ram_z[i])
            print(f"  {what}: RAM differs at ${i:04X} (plain {ram_p[i]:02X}, packed {ram_z[i]:02X})")
            bad += 1
        lo = e
    if mp.vram != mz.vram:
        i = next(i for i in range(len(mp.vram)) if mp.vram[i] != mz.vram[i])
        print(f"  {what}: VRAM differs at ${i:06X}")
        bad += 1
    if mp.memb != mz.memb:
        print(f"  {what}: MEMAC-B differs: ${mp.memb:02X} vs ${mz.memb:02X}")
        bad += 1
    for n, x in (("plain ", mp), ("packed", mz)):
        print(f"{what} {n}: {x.sio_sectors} sectors read, {x.cyc / B.FRAME:.0f} frames of CPU")
    return bad


def main():
    boot_bin = open(os.path.join(OUT, "boot.bin"), "rb").read()
    lab = {ln.split()[2].upper(): int(ln.split()[1], 16)
           for ln in open(os.path.join(OUT, "boot.lab")) if len(ln.split()) == 3}
    st2 = open(os.path.join(OUT, "xex_unpack.xex"), "rb").read()
    org = st2[2] | (st2[3] << 8)
    iu_jmp = org + st2.index(bytes((0x4C, 0xFF, 0xFF))) - 6
    bp = bytearray(boot_bin)                        # trap byte on the loader's RUN jump
    bp[lab["JMP_TGT"] - 0x0700] = 0x02
    boot3 = boot_bin.ljust(3 * SECTOR, b"\x00")

    def trapped(packed):                            # trap byte on stage 2's RUN jump
        z = bytearray(packed)
        assert z[6 + iu_jmp - org] == 0x4C
        z[6 + iu_jmp - org] = 0x02
        return bytes(z)

    bad = 0
    # intro: cold boot from sector 4
    plain = open(os.path.join(PROJ, "awintro.xex"), "rb").read()
    packed = open(os.path.join(OUT, "awintro_zx.bin"), "rb").read()
    mp = load(boot3 + plain, bytes(bp), lab, 4, lab["JMP_TGT"])
    mz = load(boot3 + trapped(packed), boot_bin, lab, 4, iu_jmp)
    bad += compare("intro", mp, mz)
    # game: the chain from GAME_SEC = right behind the packed intro, as on the disk
    gsec = 4 + len(packed) // SECTOR
    lead = boot3 + packed
    plain = open(os.path.join(PROJ, "awgame.xex"), "rb").read()
    packed = open(os.path.join(OUT, "awgame_zx.bin"), "rb").read()
    mp = load(lead + plain, bytes(bp), lab, gsec, lab["JMP_TGT"])
    mz = load(lead + trapped(packed), boot_bin, lab, gsec, iu_jmp)
    bad += compare("game ", mp, mz)
    print("verify_packed_load: " + ("all OK - both packed programs load identically"
                                   if not bad else f"{bad} MISMATCH(ES)"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
