#!/usr/bin/env python3
"""
perf_frame.py - PER-FRAME cost model of the intro renderer, stock vs Rapidus.

perf_model.py totals the whole intro; this breaks the same buckets down per
BLIT frame and compares each frame's estimated render time against its pace
budget (hold vblanks). Purpose: find frames that OVERRUN the deadline pacing
-- e.g. the user-visible hitch at ~frame 911 (Lester close-up zoom + doors),
reported on Rapidus only.

Model summary (constants from perf_model.py, confirmed against alt-src):
  stock : one 6502 stream, blitter hidden behind CPU work; half-res halves
          the spans/scanlines of every poly (absolute-even row gate).
  rapidus: CPU buckets /11; VBXE-touching share stays native. Blitter can be
          EXPOSED (CPU reaches the next busy-poll early): frame time is
          estimated as max(cpu_stream, blit_serialised) per event, summed --
          approximated as sum of per-event max(cpu, blit).

    python tools/perf_frame.py [lo hi]     # default: report 880..940 + top 20
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sim_atari
import perf_model as PM

FRAME_CYC = PM.FRAME_CYC


class FrameCounter(sim_atari.Sim):
    """Count cost drivers per BLIT frame (full-res = what Rapidus renders)."""
    def __init__(self):
        super().__init__()
        self.rows = []            # one dict per blit
        self.cur_stats = self._zero()

    def _zero(self):
        return dict(span=0, span_bytes=0, polybyte=0, plbyte=0, edge=0,
                    scan=0, scan_fast=0, copy=0, fillpg=0, drawpoly=0,
                    rs_fast=0, rs_slow=0, draw=0, hold=0)

    # --- counters (mirror perf_model.Counter) ---
    def by(self, off):
        self.cur_stats['polybyte'] += 1
        return super().by(off)

    def mul(self, m, zoom):
        self.cur_stats['rs_fast' if zoom == 64 else 'rs_slow'] += 1
        return super().mul(m, zoom)

    def draw(self, off, x, y, zoom, col):
        self.cur_stats['draw'] += 1
        return super().draw(off, x, y, zoom, col)

    def u8(self):
        self.cur_stats['plbyte'] += 1
        return super().u8()

    def u16(self):
        self.cur_stats['plbyte'] += 2
        return super().u16()

    def fill(self, off, color, zoom, ptx, pty):
        bbw = (self.poly[off & 0xFFFF] * zoom) >> 6
        bbh = (self.poly[(off + 1) & 0xFFFF] * zoom) >> 6
        x0 = ptx - bbw // 2
        y0 = pty - bbh // 2
        self._inside = (x0 >= 0 and x0 + bbw <= 319 and y0 >= 0 and y0 + bbh <= 199)
        super().fill(off, color, zoom, ptx, pty)

    def fill_poly_int(self, pts, color):
        n = len(pts)
        if n >= 3:
            self.cur_stats['edge'] += max(0, n - 2)
            ys = [p[1] for p in pts]
            sl = max(0, min(199, max(ys)) - max(0, min(ys)))
            self.cur_stats['scan'] += sl
            if getattr(self, '_inside', False):
                self.cur_stats['scan_fast'] += sl
        super().fill_poly_int(pts, color)

    def fill_span(self, sx, sy, ln, color):
        self.cur_stats['span'] += 1
        self.cur_stats['span_bytes'] += ln
        super().fill_span(sx, sy, ln, color)

    # --- playlist walk with per-frame cuts ---
    def run(self, want_frame):
        while True:
            op = self.u8()
            if op == 0x00:
                break
            elif op == 0x01:
                self.curpal = self.u8()
            elif op == 0x02:
                self.cur = self.u8()
            elif op == 0x03:
                pg = self.u8(); col = self.u8()
                self.pages[pg][:] = bytes([col]) * (160 * 200)
                self.cur_stats['fillpg'] += 1
            elif op == 0x04:
                s = self.u8(); d = self.u8()
                self.pages[d][:] = self.pages[s][:]
                self.cur_stats['copy'] += 1
            elif op == 0x05:
                off = self.u16(); x = self.s16r(); y = self.s16r(); z = self.s16r()
                self.cur_stats['drawpoly'] += 1
                self.draw(off, x, y, z, 0xFF)
            elif op == 0x07:
                str_id = self.u16(); x = self.u8(); y = self.u8(); col = self.u8()
                self.draw_text(str_id, x, y, col)
            elif op == 0x08:
                self.u8(); self.u8()
            elif op == 0x09:
                pass
            elif op == 0x06:
                pg = self.u8(); hold = self.u8()
                self.cur_stats['hold'] = max(1, hold)
                self.rows.append(self.cur_stats)
                self.cur_stats = self._zero()
                if len(self.rows) > want_frame:
                    return


def frame_cost(st, half):
    """Bucket cycles for one frame. half=True halves span/scanline work
    (stock half-res gate). Returns (cost dict, blitter-cycles serial)."""
    div = 2 if half else 1
    n_span = st['span'] / div
    span_bytes = st['span_bytes'] / div
    scan = st['scan'] / div
    scan_fast = st['scan_fast'] / div
    children = st['draw'] - st['drawpoly']
    n_rs = st['rs_fast'] + st['rs_slow']
    n_pf = st['polybyte'] - n_rs
    c = {}
    c['playlist read (pl_byte)'] = st['plbyte'] * (PM.C_PLBYTE_BANK + PM.C_PLBYTE_REST)
    c['poly fetch (poly_fetch)'] = n_pf * PM.C_POLYBYTE
    c['read_scaled coords (rs_fast/slow)'] = (st['rs_fast'] * PM.C_RS_FAST +
                                              st['rs_slow'] * PM.C_RS_SLOW)
    c['poly ptr sync (setptr+get_dr_off)'] = ((st['drawpoly'] + 2 * children) *
                                              PM.C_SETPTR + children * PM.C_GETDROFF)
    c['span row-offset calc (#3)'] = n_span * PM.C_SPAN_OFFSET
    c['span BCB patch + fire (#1)'] = n_span * (PM.C_SPAN_BCB + PM.C_FIRE)
    c['span blitter fire/wait (#1)'] = n_span * PM.C_BLIT_FIXED + span_bytes * PM.C_BLIT_PERBYTE
    c['edge divide calc_step'] = st['edge'] * (PM.C_CALCSTEP + PM.C_SLOPE_SETUP)
    c['scanline clip+addstep'] = (scan * PM.C_ADDSTEP + scan_fast * PM.C_DRAWSCAN_FAST +
                                  (scan - scan_fast) * PM.C_DRAWSCAN)
    c['copy/fill page blits'] = (st['copy'] + st['fillpg']) * PM.C_BIGBLIT
    # serialized blitter cycles (CPU-cycle equivalent): spans + full-page blits.
    # copy: per Altirra row cost = dst+src bytes -> 2*160/row; fill: 160/row.
    blit = (n_span * PM.BLIT_BCB_LOAD + span_bytes * (1 if half else 1)
            + (span_bytes if False else 0)) / PM.BLIT_CLK_MULT
    blit += st['copy'] * (PM.BLIT_BCB_LOAD + 200 * 320) / PM.BLIT_CLK_MULT
    blit += st['fillpg'] * (PM.BLIT_BCB_LOAD + 200 * 160) / PM.BLIT_CLK_MULT
    # half-res spans are 2 rows tall -> same dest bytes as full? NO: half-res
    # span covers 2 rows => dest bytes = 2*width, so blit bytes ~ equal.
    return c, blit


def stock_time(st):
    c, blit = frame_cost(st, half=True)
    cpu = sum(c.values())
    return max(cpu, blit)          # blitter mostly hidden; lower bound


def rapidus_time(st):
    c, blit = frame_cost(st, half=False)
    eff = 0.0
    for k, v in c.items():
        f = PM.VBXE_FRAC.get(k, 0.5)
        eff += v * f + v * (1 - f) / PM.RAPIDUS_CPU_MULT
    return max(eff, blit), eff, blit


def main():
    lo = int(sys.argv[1]) if len(sys.argv) > 2 else 885
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else 935
    sim = FrameCounter()
    sim.run(10 ** 9)
    rows = sim.rows
    print(f'frames: {len(rows)}   (budget = hold * {FRAME_CYC} cyc)')
    print(f'{"f":>5} {"hold":>4} {"spans":>6} {"spanB":>7} {"edges":>5} '
          f'{"copy":>4} {"stock":>7} {"rapidus":>8} {"blit":>7} {"budget":>7}  over?')
    worst = []
    for i, st in enumerate(rows):
        r, r_cpu, r_blit = rapidus_time(st)
        s = stock_time(st)
        budget = st['hold'] * FRAME_CYC
        worst.append((r / budget, i))
        if lo <= i <= hi:
            over = ''
            if r > budget:
                over = f'RAPIDUS OVER {100*r/budget-100:.0f}%'
            if s > budget:
                over += f'  STOCK OVER {100*s/budget-100:.0f}%'
            print(f'{i:>5} {st["hold"]:>4} {st["span"]:>6} {st["span_bytes"]:>7} '
                  f'{st["edge"]:>5} {st["copy"]:>4} {s:>7,.0f} {r:>8,.0f} '
                  f'{r_blit:>7,.0f} {budget:>7}  {over}')
    print('\ntop 20 rapidus frames by time/budget:')
    worst.sort(reverse=True)
    for frac, i in worst[:20]:
        st = rows[i]
        r, r_cpu, r_blit = rapidus_time(st)
        s = stock_time(st)
        print(f'  f{i:4}  hold={st["hold"]}  rapidus={r:8,.0f} ({100*frac:.0f}% of '
              f'budget)  stock={s:8,.0f} ({100*s/(st["hold"]*FRAME_CYC):.0f}%)  '
              f'spans={st["span"]} copy={st["copy"]}')


if __name__ == '__main__':
    main()
