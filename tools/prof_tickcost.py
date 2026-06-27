#!/usr/bin/env python3
"""
prof_tickcost.py - GAME: how many vblanks does ONE VM tick really cost on a stock
PAL 6502, and is the game pace-bound (waits for the hold) or CPU-bound (overruns it)?

Why it matters: the input-gated TURBO (halve vm_hold) had no visible effect. If the
render+VM time of a tick already exceeds the hold, the pace deadline is overrun every
tick and shrinking the hold changes nothing -- the only way to speed the game up is to
shrink the TICK itself. This measures that, per part, from the real bytecode, using
the cycle constants calibrated for the intro in perf_model.py.

Also measures the noclip fast-path eligibility (intro wave-2: bbox fully on-screen ->
draw_scanline_fast, clip is ~half the scanline cost; THE GAME NEVER PATCHES smc_dsl,
so every game scanline pays the full clip today).

  python tools/prof_tickcost.py            # all parts, 250 frames each
  python tools/prof_tickcost.py 16002 400
"""
import os, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_atari, sim_atari

PARTS = [(16002, "water"), (16003, "jail"), (16004, "cite"),
         (16005, "arene"), (16006, "luxe"), (16007, "final")]
FRAME_CYC = 35568              # PAL vblank
# perf_model.py constants (2026-06-10 calibration)
C_CODEBYTE   = 30              # VM bytecode fetch + dispatch share (aw2/aw3 opts)
C_POLYBYTE   = 26
C_RS_FAST    = 37
C_RS_SLOW    = 145
C_DRAW       = 31 + 45        # set_poly_ptr + per-draw/hier-child overhead
C_EDGE       = 90 + 40        # calc_step + slope setup
C_SCAN_CLIP  = 60 + 58        # draw_scanline (FULL CLIP -- game never patches) + add_steps
C_SCAN_FAST  = 32 + 58        # draw_scanline_fast + add_steps (the intro noclip path)
C_SPAN       = 24 + 34 + 8    # row LUT + BCB patch + fire (blit pipelined behind it)
C_SPANBYTE   = 0.125          # residual blitter wait ~ (W/8) CPU-cyc on big spans
C_COPYPAGE   = 8000           # full-page copy: 2*32000 blitter bytes / 8 + overhead
C_FILLPAGE   = 4000
HALFRES      = 0.55           # stock 6502 draws ~55% of scanlines (2-tall spans, parity)


class TickCost(game_atari.GameAtari):
    def __init__(self, part):
        super().__init__(part)
        self.c = collections.Counter()
        self.per_frame = []
        self._inside = False

    # --- fetch / math counters ---
    def b(self):
        self.c['code'] += 1
        return super().b()

    def w(self):
        self.c['code'] += 2
        v = (self.code[self.pc] << 8) | self.code[self.pc + 1]
        self.pc += 2
        return v

    def by(self, off):
        self.c['poly'] += 1
        return super().by(off)

    def mul(self, m, zoom):
        self.c['rs_fast' if zoom == 64 else 'rs_slow'] += 1
        return sim_atari.Sim.mul(self, m, zoom)

    def draw(self, off, x, y, zoom, col):
        self.c['draw'] += 1
        return sim_atari.Sim.draw(self, off, x, y, zoom, col)

    def fill(self, off, color, zoom, ptx, pty):
        bbw = (self._pd[off & 0xFFFF] * zoom) >> 6
        bbh = (self._pd[(off + 1) & 0xFFFF] * zoom) >> 6
        x0 = ptx - bbw // 2
        y0 = pty - bbh // 2
        self._inside = (x0 >= 0 and x0 + bbw <= 319 and y0 >= 0 and y0 + bbh <= 199)
        return sim_atari.Sim.fill(self, off, color, zoom, ptx, pty)

    def fill_poly_int(self, pts, color):
        n = len(pts)
        if n >= 3:
            self.c['edge'] += max(0, n - 2)
            ys = [p[1] for p in pts]
            sl = max(0, min(199, max(ys)) - max(0, min(ys)))
            self.c['scan'] += sl
            if self._inside:
                self.c['scan_in'] += sl
        return sim_atari.Sim.fill_poly_int(self, pts, color)

    def fill_span(self, sx, sy, ln, color):
        self.c['span'] += 1
        self.c['spanb'] += ln
        return sim_atari.Sim.fill_span(self, sx, sy, ln, color)

    def op_fillpage(self):
        self.c['fillpg'] += 1
        return super().op_fillpage()


def cost_cyc(d, halfres, noclip=False):
    """cycles for one frame's counter delta d"""
    scan = d['scan'] * (HALFRES if halfres else 1.0)
    span = d['span'] * (HALFRES if halfres else 1.0)
    spanb = d['spanb'] * (HALFRES if halfres else 1.0)
    c_in = d['scan_in'] * (HALFRES if halfres else 1.0)
    if noclip:   # eligible scanlines take the fast path
        scan_cyc = c_in * C_SCAN_FAST + (scan - c_in) * C_SCAN_CLIP
    else:
        scan_cyc = scan * C_SCAN_CLIP
    return (d['code'] * C_CODEBYTE + d['poly'] * C_POLYBYTE
            + d['rs_fast'] * C_RS_FAST + d['rs_slow'] * C_RS_SLOW
            + d['draw'] * C_DRAW + d['edge'] * C_EDGE + scan_cyc
            + span * C_SPAN + spanb * C_SPANBYTE
            + d['copypg'] * C_COPYPAGE + d['fillpg'] * C_FILLPAGE)


def profile(part, frames):
    vm = TickCost(part)
    # frame boundary + copy-page counting via the OPS table
    base_upd = vm.OPS[0x10]
    base_cpy = vm.OPS[0x0F]
    last = collections.Counter()

    def upd(self):
        r = base_upd(self)
        d = self.c - last
        last.clear(); last.update(self.c)
        self.per_frame.append((dict(d), self.var[0xFF] & 0xFF))
        return r

    def cpy(self):
        self.c['copypg'] += 1
        return base_cpy(self)

    vm.OPS = list(vm.OPS)
    vm.OPS[0x10] = upd
    vm.OPS[0x0F] = cpy
    vm.run(frames)
    return vm


def pct(v, p):
    s = sorted(v)
    return s[min(len(s) - 1, int(len(s) * p))] if s else 0


def main():
    if len(sys.argv) > 1:
        part = int(sys.argv[1]); n = int(sys.argv[2]) if len(sys.argv) > 2 else 250
        parts = [(part, dict(PARTS).get(part, f"p{part}"))]
    else:
        parts = PARTS; n = 250

    print("== per-TICK cycle cost vs hold (stock PAL 6502, perf_model constants) ==")
    print(f"   {n} no-input frames/part; half-res factor {HALFRES} (stock auto half-res).\n")
    print(f"  {'part':7}{'hold(med)':>10}{'tick-vbl med':>13}{'p90':>6}{'overrun%':>10}"
          f"{'noclip-elig%':>14}{'tick-vbl noclip':>17}{'overrun% noclip':>17}")
    for p, name in parts:
        vm = profile(p, n)
        vbl, vbl_nc, holds, over, over_nc = [], [], [], 0, 0
        scan_t = scan_in = 0
        for d, hold in vm.per_frame:
            d = collections.Counter(d)
            scan_t += d['scan']; scan_in += d['scan_in']
            v = cost_cyc(d, halfres=True) / FRAME_CYC
            v2 = cost_cyc(d, halfres=True, noclip=True) / FRAME_CYC
            h = max(1, hold)
            vbl.append(v); vbl_nc.append(v2); holds.append(h)
            if v > h: over += 1
            if v2 > h: over_nc += 1
        nf = max(1, len(vbl))
        print(f"  {name:7}{pct(holds,0.5):>10}{pct(vbl,0.5):>13.2f}{pct(vbl,0.9):>6.2f}"
              f"{100*over/nf:>9.0f}%{100*scan_in/max(1,scan_t):>13.0f}%"
              f"{pct(vbl_nc,0.5):>17.2f}{100*over_nc/nf:>16.0f}%")
    print("\nReading it: overrun% = frames whose estimated render+VM time EXCEEDS the hold")
    print("-> the pace deadline is missed, the game runs slower than scripted, and any")
    print("hold-based turbo does nothing. noclip = porting the intro's draw_scanline_fast")
    print("dispatch (bbox fully on-screen) to the game.")


if __name__ == "__main__":
    main()
