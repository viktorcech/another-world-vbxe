#!/usr/bin/env python3
"""verify_unpack.py - end-to-end check of the ZX02-packed part data: the REAL game
code (awgame.xex) on the 6502 core of _bench_frame.Machine, SIO served from the REAL
awgame_full.atr. For every part it boots straight into that part, runs to the first
op_updatedisplay and compares what load_part unpacked (VRAM v1/code/v2 banks, the
sound banks, pal_data) with the raw resources in out/game_parts.bin; then it calls
load_bitmap for every background bitmap and compares framebuffer page 0.
Also prints the SIO sectors and CPU cycles each load took.

Run from the project root after build.ps1:   python tools/verify_unpack.py
"""
import os, sys, json, re
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _bench_frame as B                                          # noqa: E402
import _sim6502 as S                                              # noqa: E402

OUT = os.path.join(PROJ, "out")
SND_BLIST = [0x0E, 0x0F, 0x11, 0x12, 0x13, 0x1E, 0x1F]
POLY_BANK0, PLAY_BANK0 = 0x14, 0x18


def main():
    meta = json.load(open(os.path.join(OUT, "game_parts.json")))
    blob = open(os.path.join(OUT, "game_parts.bin"), "rb").read()
    raw = lambda e: blob[e[0] * 128:(e[0] + e[1]) * 128]
    bad = 0

    def check(name, want, got):
        nonlocal bad
        if want == got:
            return "ok"
        bad += 1
        i = next(i for i in range(len(want)) if i >= len(got) or want[i] != got[i])
        return f"DIFF@{i}"

    def check_part(part, mc):
        vr = bytes(mc.vram)
        pal, code, v1, v2 = meta["table"][str(part)]
        snd = meta["snd_table"][str(part)]
        out = [f"v1 {check('v1', raw(v1), vr[POLY_BANK0 << 14:][:v1[1] * 128])}",
               f"code {check('code', raw(code), vr[PLAY_BANK0 << 14:][:code[1] * 128])}"]
        if v2[1]:
            out.append(f"v2 {check('v2', raw(v2), vr[(POLY_BANK0 + 8) << 14:][:v2[1] * 128])}")
        if snd[1]:
            sb = b"".join(vr[b << 14:(b + 1) << 14] for b in SND_BLIST)
            out.append(f"snd {check('snd', raw(snd), sb[:snd[1] * 128])}")
        out.append(f"pal {check('pal', raw(pal), bytes(mc.m[0x9000:0x9000 + pal[1] * 128]))}")
        return "  ".join(out)

    for part in meta["parts"]:
        res = B.frame_bench(part=part, frames=0, warm=0, prof=False, verbose=False,
                            max_cycles=B.FRAME * 3000)
        mc = res["machine"]
        if res["frames"] < 1:
            print(f"{part}: never reached its first frame (pc ${mc.pc:04X})")
            bad += 1
            continue
        print(f"{part}: {check_part(part, mc)}   {getattr(mc, 'sio_sectors', 0)} sectors, "
              f"{mc.cyc / B.FRAME:.0f} frames of CPU to the first frame")

    # part SWITCHES, as the VM makes them: what stays resident (v2) must not be
    # read again, and must still be right
    res = B.frame_bench(part=16002, frames=0, warm=0, prof=False, verbose=False,
                        max_cycles=B.FRAME * 3000)
    mc = res["machine"]
    for nxt in (16003, 16004, 16005, 16006, 16007, 16005, 16006):
        s0 = mc.sio_sectors
        mc.m[0xB3CF], mc.m[0xB3D0], mc.m[0xB3C7] = nxt & 0xFF, nxt >> 8, 1   # vm_next, vm_switch
        # the switch is taken at the start of the NEXT VM pass: run frame marks until
        # the load has happened (every switch reads at least v1/code) and a frame follows
        for _ in range(20):
            try:
                mc.run_until(mc.cyc + B.FRAME * 3000)
                break                                # no frame at all
            except Exception as ex:                  # frame_bench's frame mark
                if type(ex).__name__ != "Mark":
                    raise
                (mc.a, mc.y, mc.zv, mc.nv, mc.pc, mc.cyc) = mc._post
            if mc.sio_sectors > s0:
                break
        if mc.sio_sectors == s0:
            print(f"switch -> {nxt}: the VM never took the switch")
            bad += 1
            break
        print(f"switch -> {nxt}: {check_part(nxt, mc)}   {mc.sio_sectors - s0} sectors")

    # bitmaps: a stub in the dead boot-loader page calls load_bitmap(i), then traps
    res = B.frame_bench(part=meta["parts"][1], frames=0, warm=0, prof=False, verbose=False,
                        max_cycles=B.FRAME * 3000)
    mc = res["machine"]
    lb = mc.lab["load_bitmap"]
    inc = open(os.path.join(PROJ, "src_game", "game_atr.inc")).read()
    nums = [int(x) for x in re.search(r"atr_bmp_num_lo\s*\n\s*dta ([\d,]+)", inc).group(1).split(",")]
    hi = [int(x) for x in re.search(r"atr_bmp_num_hi\s*\n\s*dta ([\d,]+)", inc).group(1).split(",")]
    for i, (lo, h) in enumerate(zip(nums, hi)):
        n = lo | (h << 8)
        stub = 0x0600
        mc.m[stub:stub + 6] = bytes((0xA9, i, 0x20, lb & 0xFF, lb >> 8, 0x02))
        mc.pc, mc.i = stub, 1
        c0, s0 = mc.cyc, getattr(mc, "sio_sectors", 0)
        mc.trap = lambda pc, _o=B.Machine.trap: (_ for _ in ()).throw(StopIteration) \
            if pc == stub + 5 else _o(mc, pc)
        try:
            mc.run_until(mc.cyc + B.FRAME * 2000)
        except StopIteration:
            pass
        e = meta["bmp"][str(n)]
        r = check("bmp", raw(e), bytes(mc.vram[:e[1] * 128]))
        print(f"bitmap {n:3d}: {r}   {getattr(mc, 'sio_sectors', 0) - s0} sectors, "
              f"{(mc.cyc - c0) / B.FRAME:.1f} frames of CPU")
    print("verify_unpack: " + ("all OK" if not bad else f"{bad} MISMATCH(ES)"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
