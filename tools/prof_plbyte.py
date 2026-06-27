#!/usr/bin/env python3
"""
prof_plbyte.py - GAME profiler + correctness check for the pl_byte bytecode fetch.

The AW VM fetches bytecode through pl_byte 3-4x per opcode. The ORIGINAL pl_byte
recomputed the MEMAC-B bank (six lsr on (pl_hi<<2)|(pl_mid>>6)) AND the window pointer
on EVERY byte. aw2.txt replaces that with the poly_fetch model: a running window
pointer (pl_whi:pl_wlo) + a cached bank (pl_bank), recomputed only on a 16 KB window
crossing, with set_pl_ptr doing the heavy math once per jump.

This test answers, FOR THE GAME:

  1. CORRECTNESS  -- does (cached bank, running window pointer) always point at the
                     same VRAM byte as the absolute address PLAY_BASE + pc?
                     Checked both for set_pl_ptr(pc) and for byte-by-byte advancing.
  2. RELEVANCE    -- how many bytecode bytes does each gameplay PART fetch per frame?
                     That x ~32 cyc/byte saved is the win (huge in cite/jail).

Run:   python tools/prof_plbyte.py            # all parts
       python tools/prof_plbyte.py 16004 600  # one part, N frames
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_sim
import aw_sim

# VRAM geometry (must match src_game: PLAY_BASE $060000, banks $18+; poly base $14)
POLY_BANK0 = 0x14
PLAY_BANK0 = 0x18
PLAY_BASE = 0x060000
DATAW = 0x4000

PARTS = [(16002, "water"), (16003, "jail"), (16004, "cite"),
         (16005, "arene"), (16006, "luxe"), (16007, "final")]
PAL_BUDGET = 35568                  # cycles per 50 Hz PAL frame
BYTE_SAVING = 32                    # ~cycles saved per pl_byte call by the aw2.txt rewrite


# --- 1. correctness: the running pointer must equal the absolute VRAM address --------
def set_pl_ptr(pc):                 # what src_game set_pl_ptr computes (reuses poly LUTs)
    hi = (pc >> 8) & 0xFF; lo = pc & 0xFF
    pl_bank = ((((hi >> 6) + POLY_BANK0) | 0x80) + (PLAY_BANK0 - POLY_BANK0)) & 0xFF
    pl_whi = ((hi & 0x3F) | (DATAW >> 8)) & 0xFF
    return pl_bank, pl_whi, lo


def want(pc):                       # the true MEMAC-B bank + CPU window address for pc
    addr = PLAY_BASE + pc
    win = DATAW + (addr & 0x3FFF)
    return (0x80 | ((addr >> 14) & 0xFF)), (win >> 8) & 0xFF, win & 0xFF


def check_correctness():
    bad_seek = sum(1 for pc in range(1 << 16) if set_pl_ptr(pc) != want(pc))
    # byte-by-byte advance (the inc pl_wlo / window-cross / bank++ logic in pl_byte)
    bank, whi, wlo = set_pl_ptr(0); drift = 0
    for pc in range(1 << 16):
        if (bank, whi, wlo) != want(pc):
            drift += 1
        wlo = (wlo + 1) & 0xFF
        if wlo == 0:
            whi = (whi + 1) & 0xFF
            if whi == 0x80:
                whi = DATAW >> 8; bank = (bank + 1) & 0xFF
    ok = bad_seek == 0 and drift == 0
    print(f"[correctness] set_pl_ptr over all pc: {'OK' if not bad_seek else f'{bad_seek} WRONG'}"
          f" ; byte-by-byte advance: {'OK' if not drift else f'{drift} DRIFT'}")
    return ok


# --- 2. relevance: bytecode-fetch volume per frame (= pl_byte calls) -----------------
def profile_part(part, frames):
    cnt = [0]
    ob, ow = aw_sim.VM.b, aw_sim.VM.w

    def b(self, _c=cnt):
        _c[0] += 1; return ob(self)

    def w(self, _c=cnt):
        _c[0] += 2; return ow(self)        # w() = two pl_byte fetches

    game_sim.GameVM.b = b
    game_sim.GameVM.w = w
    try:
        vm = game_sim.GameVM(part)
        vm.run(frames)
    finally:
        game_sim.GameVM.b = ob
        game_sim.GameVM.w = ow
    nfr = max(len(vm.frames), 1)
    per = cnt[0] // nfr
    saved = per * BYTE_SAVING
    return dict(frames=len(vm.frames), total=cnt[0], per_frame=per,
                cyc=saved, budget=100 * saved / PAL_BUDGET)


def main():
    print("== pl_byte GAME profiler ==\n")
    check_correctness()
    print()
    if len(sys.argv) > 1:
        part = int(sys.argv[1]); frames = int(sys.argv[2]) if len(sys.argv) > 2 else 250
        parts = [(part, dict(PARTS).get(part, f"p{part}"))]
    else:
        parts = PARTS; frames = 250

    print(f"[relevance] bytecode bytes fetched over {frames} no-input frames per part:\n")
    print(f"  {'part':8}{'frames':>7}{'bytes/frame':>12}{'~cyc/frame':>12}{'% PAL frame':>12}")
    worst = 0
    for p, name in parts:
        r = profile_part(p, frames)
        worst = max(worst, r["per_frame"])
        print(f"  {name:8}{r['frames']:7}{r['per_frame']:12}{r['cyc']:12}{r['budget']:11.1f}%")

    print()
    print("Every part benefits in proportion to its bytecode volume. cite/jail are VM-heavy")
    print(f"(cite's pl_byte overhead alone exceeds a frame budget). Worst here: {worst} bytes/")
    print("frame -> the aw2.txt rewrite is the single biggest game win; it is in")
    print("src_game/aw_polygon.asm (pl_byte + set_pl_ptr) + the game_vm.asm jump sites.")


if __name__ == "__main__":
    main()
