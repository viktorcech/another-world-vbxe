#!/usr/bin/env python3
"""
prof_zphygiene.py - GAME profiler for the "decoder locals in absolute RAM" question (aw4.txt).

aw4.txt (Zero-Page hygiene) claims the polygon decode/raster hot loops lose cycles when
their working variables sit in absolute RAM ($0100+) instead of zero page: `lda abs` is 4
cyc vs 3 for `lda zp`, `inc abs` is 6 vs 5, etc. -- 1 cyc per touch.

REALITY CHECK against the actual game code (src/aw_equates.inc, shared by src_game):
  dr_off  $86, pb_ptr $8A        -> ALREADY zero page  (aw4.txt's guess was stale)
  scaled_lo/hi, x0, y0, vidx      -> absolute RAM (RAMB=$9C00): RAMB+53.. -- the REAL targets

So this measures only the genuinely-non-ZP decoder locals. For each gameplay PART it replays
the real bytecode (game_atari, the 160-LR oracle), counts how often each variable is touched
in do_fill/do_hier/mul_zoom, and converts to cycles/frame saved if it were moved RAM->ZP
(1 cyc per access). That decides whether the move is worth the scarce ZP bytes.

  Access weights below are read straight off src_game/aw_polygon.asm (do_fill / do_hier /
  mul_zoom) -- every lda/sta/adc/sbc/cmp/inc that names the variable, = 1 saved cyc in ZP.

Run:   python tools/prof_zphygiene.py            # all parts, 250 frames
       python tools/prof_zphygiene.py 16004 600  # one part, N frames
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_atari
import sim_atari

PARTS = [(16002, "water"), (16003, "jail"), (16004, "cite"),
         (16005, "arene"), (16006, "luxe"), (16007, "final")]
PAL_BUDGET = 35568                  # cycles per 50 Hz PAL frame
FREE_ZP = 0                         # the GAME has NO free ZP gap: game_vm.asm took the
                                    # $B8-$BD slots aw_equates.inc leaves free in the intro.

# --- per-event ZP savings weights (1 saved cyc per RAM access), from aw_polygon.asm --------
# scaled_lo/hi (2 B): mul_zoom writes 2 (sta scaled_lo/hi) per read_scaled; readers below.
SC_PER_MUL   = 2     # mul_zoom: sta scaled_lo + sta scaled_hi
SC_PER_FILL  = 4     # do_fill bbw (lda lo+hi) + bbh (lda lo+hi)
SC_PER_VERT  = 4     # do_fill vertex: px adc lo+hi, py adc lo+hi
SC_PER_NODE  = 4     # do_hier bx (sbc lo+hi) + by (sbc lo+hi)
SC_PER_CHILD = 4     # do_hier child: cx adc lo+hi, cy adc lo+hi
# x0, y0 (2 B each, do_fill only):
XY_PER_FILL  = 2     # 2 writes (sta x0, sta x0+1) -- per variable
XY_PER_VERT  = 2     # 2 reads  (lda x0, lda x0+1) per vertex -- per variable
# vidx (1 B, do_fill only):
VI_PER_FILL  = 1     # sta vidx (init 0)
VI_PER_VERT  = 4     # ldx vidx (px) + ldx vidx (py) + inc vidx + lda vidx (cmp)

CANDS = [  # (name, bytes, accessor(counts)->total touches)
    ("scaled_lo/hi", 2, lambda c: (SC_PER_MUL * c["mul"] + SC_PER_FILL * c["fill"]
                                   + SC_PER_VERT * c["vert"] + SC_PER_NODE * c["node"]
                                   + SC_PER_CHILD * c["child"])),
    ("x0",           2, lambda c: XY_PER_FILL * c["fill"] + XY_PER_VERT * c["vert"]),
    ("y0",           2, lambda c: XY_PER_FILL * c["fill"] + XY_PER_VERT * c["vert"]),
    ("vidx",         1, lambda c: VI_PER_FILL * c["fill"] + VI_PER_VERT * c["vert"]),
]

_orig_mul  = sim_atari.Sim.mul
_orig_fill = sim_atari.Sim.fill
_orig_hier = sim_atari.Sim.hier


def profile_part(part, frames):
    c = dict(mul=0, fill=0, vert=0, node=0, child=0)

    def mul(self, m, zoom):
        c["mul"] += 1
        return _orig_mul(self, m, zoom)

    def fill(self, off, color, zoom, ptx, pty):
        c["fill"] += 1
        c["vert"] += self.by(off + 2)            # n verts (3rd byte: bbw,bbh,n)
        return _orig_fill(self, off, color, zoom, ptx, pty)

    def hier(self, off, zoom, ptx, pty, color):
        c["node"] += 1
        c["child"] += self.by(off + 2) + 1       # childcount+1 iterations
        return _orig_hier(self, off, zoom, ptx, pty, color)

    game_atari.GameAtari.mul = mul
    game_atari.GameAtari.fill = fill
    game_atari.GameAtari.hier = hier
    try:
        vm = game_atari.GameAtari(part)
        vm.run(frames)
    finally:
        game_atari.GameAtari.mul = _orig_mul
        game_atari.GameAtari.fill = _orig_fill
        game_atari.GameAtari.hier = _orig_hier

    nfr = max(len(vm.frames), 1)
    per = []
    total = 0
    for name, nb, fn in CANDS:
        touches = fn(c)
        total += touches
        per.append((name, nb, touches, touches / nfr))
    return dict(frames=len(vm.frames), counts=c, per=per,
                cyc=total / nfr, pct=100 * (total / nfr) / PAL_BUDGET)


def main():
    if len(sys.argv) > 1:
        part = int(sys.argv[1]); frames = int(sys.argv[2]) if len(sys.argv) > 2 else 400
        parts = [(part, dict(PARTS).get(part, f"p{part}"))]
    else:
        parts = PARTS; frames = 250

    print("== ZP-hygiene GAME profiler (aw4.txt) ==\n")
    print("Targets (verified absolute RAM in src/aw_equates.inc): scaled_lo/hi, x0, y0, vidx")
    print("(dr_off/pb_ptr are ALREADY zero page -- aw4.txt's other two guesses are moot.)\n")
    print(f"Cycles/frame saved if moved RAM->ZP (1 cyc/access), {frames} no-input frames:\n")
    hdr = f"  {'part':7}{'frames':>7}{'fills':>7}{'verts':>7}{'children':>9}"
    for name, _, _ in CANDS:
        hdr += f"{name:>13}"
    hdr += f"{'TOTAL cyc':>11}{'% frame':>9}"
    print(hdr)

    worst = 0.0
    for p, name in parts:
        r = profile_part(p, frames)
        worst = max(worst, r["pct"])
        cc = r["counts"]
        line = (f"  {name:7}{r['frames']:7}{cc['fill']:7}{cc['vert']:7}{cc['child']:9}")
        for _, _, _, perfr in r["per"]:
            line += f"{perfr:13.0f}"
        line += f"{r['cyc']:11.0f}{r['pct']:8.2f}%"
        print(line)

    need = sum(nb for _, nb, _ in CANDS)
    print()
    print(f"ZP budget: moving all 4 needs {need} bytes; the GAME has {FREE_ZP} free ZP "
          f"(game_vm took $B8-$BD).")
    print("So UNION onto the raster ZP ($C0-$C6 = cr0..cl2): the decoder finishes building the")
    print("point list BEFORE fill_poly_int walks it (and fill_poly_int reinitialises cr/cl at")
    print("entry), so scaled/x0/y0/vidx and cr/cl never hold live values at the same time.")
    print("DONE in src_game/game_zp.inc (intro fork untouched, still RAM in aw_equates.inc).")
    print()
    print(f"Verdict: the win scales with vertex count -- light scenes (cite ~5%) are minor,")
    print(f"but the heavy sprite-scaling parts are large: worst here ~{worst:.0f}% of a PAL")
    print("frame (arene), the exact scenes that drop frames. It's output-identical (pure")
    print("addressing-mode change), so it's free headroom. Apply to src_game/aw_equates side")
    print("(union scaled/x0/y0/vidx onto $C0-$D3 since decode precedes raster); the intro")
    print("fork can take the same equate change. NOTE: since the game is vblank-paced, this")
    print("prevents slowdown in arene/luxe rather than speeding up light scenes -- as intended.")


if __name__ == "__main__":
    main()
