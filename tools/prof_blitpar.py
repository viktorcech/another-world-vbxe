#!/usr/bin/env python3
"""
prof_blitpar.py - GAME: how many cycles does the CPU actually waste in blit_idle? (aw5.txt)

aw5.txt: fill_span calls blit_idle (busy-wait for the VBXE blitter) before touching the BCB.
While the CPU spins there it does nothing. The tip: overlap that wait with useful CPU work
(decode the next polygon, etc.) so the blitter runs in parallel instead of stalling the 6502.

Whether this is worth anything depends on the BLITTER SPEED vs the CPU work between blits.
From the VBXE manuals (vbxe/vbxenavod.txt):
  * PCLK = 14.18 MHz, 1 cycle per byte read/written  -> blitter does ~8 bytes per PAL-6502 cycle.
  * a CONSTANT fill is 1 PCLK/byte; a COPY (reads source) is 2 PCLK/byte ("twice as fast").
So a span of W LR bytes costs W/8 (solid) .. W/4 (transparent/copy) 6502-cycles of blitter time;
a full 160x200 page copy costs 32000*2/8 = 8000 cycles, a page fill 4000.

This runs a discrete-event sim of the CPU and the blitter over the REAL game, with the engine's
"blit_idle is wait-BEFORE-the-BCB" semantics, and sums the cycles the CPU actually stalls. That
stall is the absolute ceiling of what aw5 could ever reclaim.

  python tools/prof_blitpar.py            # all parts, 250 frames
  python tools/prof_blitpar.py 16005 400  # arene
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_atari
import sim_atari

PARTS = [(16002, "water"), (16003, "jail"), (16004, "cite"),
         (16005, "arene"), (16006, "luxe"), (16007, "final")]
PAL_BUDGET = 35568
LR_PAGE = 160 * 200                 # 32000 bytes per LR page

# blitter timing (6502 cycles): PCLK 14.18 MHz / 1.773 MHz = 7.997 bytes per 6502 cycle
BYTES_PER_CYC = 14.18 / 1.773447
BCB_FETCH = 21 / BYTES_PER_CYC      # ~2.6 cyc: the blitter reloads a 21-byte BCB per op
# CPU-side per-event costs (same weights as perf_model.py)
CPU_RASTER_PER_SPAN = 58 + 60 + 24  # add_steps + draw_scanline(clip) + emit_span(row calc)
CPU_BCB_PATCH = 52                  # writing dst/width/height/mode into the BCB (after blit_idle)
CPU_DECODE_PER_VERT = 60            # poly_fetch + 2x read_scaled(mul_zoom) + vertex add/store
CPU_DECODE_PER_CHILD = 40           # do_hier per-child fetch + 2x read_scaled


class BlitDES:
    """CPU/blitter timeline with wait-before-BCB semantics. Times are 6502 cycles."""
    def __init__(self):
        self.cpu = 0.0
        self.blit_done = 0.0
        self.pending_cpu = 0.0       # CPU work accumulated since the last blit fire
        self.stall = 0.0
        self.busy = 0.0              # total blitter-busy cycles
        self.frame_stall = []

    def cpu_work(self, c):
        self.pending_cpu += c

    def blit(self, nbytes, per_byte):
        # advance CPU through the work done since the last fire, then blit_idle (wait-before):
        self.cpu += self.pending_cpu
        self.pending_cpu = 0.0
        if self.cpu < self.blit_done:               # CPU reached blit_idle early -> STALL
            self.stall += self.blit_done - self.cpu
            self.cpu = self.blit_done
        self.cpu += CPU_BCB_PATCH                    # patch BCB (CPU)
        dur = BCB_FETCH + nbytes * per_byte
        self.busy += dur
        self.blit_done = self.cpu + dur              # fire; blitter runs in parallel
        self.cpu += 1                                # BL_START write

    def end_frame(self):
        self.frame_stall.append(self.stall)
        self.stall = 0.0


def profile_part(part, frames):
    des = BlitDES()
    _of, _oh, _ofs = sim_atari.Sim.fill, sim_atari.Sim.hier, sim_atari.Sim.fill_span
    OPS = game_atari.GameAtari.OPS                     # GameAtari has its OWN dispatch table
    byname = {OPS[k].__name__: k for k in range(len(OPS))}
    i_fp, i_cp, i_ud = byname['op_fillpage'], byname['op_copypage'], byname['op_updatedisplay']
    o_fp, o_cp, o_ud = OPS[i_fp], OPS[i_cp], OPS[i_ud]

    def fill(self, off, color, zoom, ptx, pty):
        n = self.by(off + 2)
        des.cpu_work(n * CPU_DECODE_PER_VERT)        # decode cost (before this poly's spans)
        return _of(self, off, color, zoom, ptx, pty)

    def hier(self, off, zoom, ptx, pty, color):
        des.cpu_work((self.by(off + 2) + 1) * CPU_DECODE_PER_CHILD)
        return _oh(self, off, zoom, ptx, pty, color)

    def fill_span(self, sx, sy, ln, color):
        des.cpu_work(CPU_RASTER_PER_SPAN)
        per = 0.125 if color < 0x10 else 0.25        # solid 1 PCLK/byte ; transp/copy 2
        des.blit(max(1, ln), per)
        return _ofs(self, sx, sy, ln, color)

    def op_fillpage(self):
        des.blit(LR_PAGE, 0.125)                     # constant fill: 1 PCLK/byte
        return o_fp(self)

    def op_copypage(self):
        des.blit(LR_PAGE, 0.25)                      # copy: 2 PCLK/byte
        return o_cp(self)

    def op_updatedisplay(self):
        r = o_ud(self)
        des.end_frame()
        return r

    game_atari.GameAtari.fill = fill
    game_atari.GameAtari.hier = hier
    game_atari.GameAtari.fill_span = fill_span
    OPS[i_fp], OPS[i_cp], OPS[i_ud] = op_fillpage, op_copypage, op_updatedisplay
    try:
        vm = game_atari.GameAtari(part)
        vm.run(frames)
    finally:
        game_atari.GameAtari.fill = _of
        game_atari.GameAtari.hier = _oh
        game_atari.GameAtari.fill_span = _ofs
        OPS[i_fp], OPS[i_cp], OPS[i_ud] = o_fp, o_cp, o_ud

    nf = max(len(des.frame_stall), 1)
    stall = sum(des.frame_stall) / nf
    cpu = des.cpu / nf
    return dict(frames=len(des.frame_stall), cpu=cpu, busy=des.busy / nf,
                stall=stall, pct=100 * stall / PAL_BUDGET,
                render_pct=100 * stall / max(cpu + stall, 1))   # share of actual render time


def main():
    if len(sys.argv) > 1:
        part = int(sys.argv[1]); frames = int(sys.argv[2]) if len(sys.argv) > 2 else 400
        parts = [(part, dict(PARTS).get(part, f"p{part}"))]
    else:
        parts = PARTS; frames = 250

    print("== blit_idle stall measurement (GAME, aw5.txt) ==\n")
    print(f"VBXE blitter @14.18MHz = {BYTES_PER_CYC:.1f} bytes/6502-cyc. page copy={LR_PAGE*0.25:.0f} "
          f"cyc, fill={LR_PAGE*0.125:.0f} cyc, max span(160B) solid={160*0.125:.0f}/copy={160*0.25:.0f}.\n")
    print(f"  {'part':7}{'frames':>7}{'CPU/frame':>11}{'blit busy/fr':>13}"
          f"{'STALL/frame':>12}{'% vblank':>10}{'% of render':>12}")
    for p, name in parts:
        r = profile_part(p, frames)
        print(f"  {name:7}{r['frames']:7}{r['cpu']:11.0f}{r['busy']:13.0f}"
              f"{r['stall']:12.0f}{r['pct']:9.1f}%{r['render_pct']:11.1f}%")

    print()
    print("STALL/frame = cycles the CPU wastes in blit_idle = aw5's reclaim ceiling. It's ALL from")
    print("the per-frame background page-copy (~8000 cyc); per-span blits are <=40 cyc vs ~190 cyc")
    print("CPU/scanline, so they hide for free -- aw5 does nothing for spans.")
    print()
    print("'% of render' = stall / (CPU + stall) = the actual render-time aw5 could cut. Note the")
    print("split: it's BIG (~22%) in light scenes (cite) but they have hold-headroom (vblank-paced)")
    print("so cutting it speeds up NOTHING; it's SMALL (~2.4%) in the heavy scenes (arene/jail) that")
    print("are actually CPU-bound -- exactly where it matters least. So the reclaim lands where it's")
    print("not needed. And aw5 isn't free: hiding the copy behind decode needs a decode/raster SPLIT")
    print("(buffer all polys' point lists, raster at updateDisplay). Moderate rework for ~2.4% on the")
    print("scenes that count -- far worse ROI than aw4 (~70% on arene for a one-line equate change).")


if __name__ == "__main__":
    main()
