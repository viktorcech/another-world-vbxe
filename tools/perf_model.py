#!/usr/bin/env python3
"""
perf_model.py - estimate the 6502 cost of the current awvbxe.asm renderer.

Replays the real playlist through the faithful sim_atari model, COUNTS the
operations that dominate runtime (per-span blitter fires, per-byte MEMAC-B bank
switches, per-edge divides, big copy/fill blits), and applies approximate 6502
cycle costs taken from the asm instruction sequences. Output: where the time
goes + an estimated frame rate, and what each optimisation would save.

    python tools/perf_model.py
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sim_atari

CPU_HZ = 1773447          # PAL 6502 (~1.77 MHz)
FRAME_CYC = 35568         # cycles per PAL frame (312 lines * 114 cyc, ~49.86 Hz)

# --- Rapidus model -----------------------------------------------------------
# CONFIRMED from Altirra's emulation (alt-src/Altirra/source/rapidus.cpp + vbxe.cpp):
#   * CPU high-speed mode multiplier = 11 at 20 MHz (23 at 40 MHz):
#       ResetCPU(): OverrideCPUMode(..., g_ATCVDevicesRapidus40MHz ? 23 : 11)
#   * Rapidus SRAM/SDRAM layers are marked SetLayerFastBus(true) -> full CPU speed.
#   * VBXE has ZERO SetLayerFastBus calls -> its layers are "chip" (native) speed,
#     so EVERY VBXE/MEMAC access (poly fetch read, BCB edits, blitter regs, bank
#     switch) runs at the ~1.77 MHz native bus, NOT accelerated.
# So only CPU-bound work (math in fast RAM) gets the 11x; VBXE/bus work does not.
# VBXE_FRAC below = the share of each bucket that is slow VBXE/bus access. The
# CLASSIFICATION (which buckets touch VBXE) is confirmed; the exact fractions in
# the MIXED buckets (e.g. data-read = VRAM read + CPU ptr math) are still estimates.
RAPIDUS_CPU_MULT = 11.0                                    # Altirra 20 MHz mode (40 MHz = 23)
RAPIDUS_CPU_MHZ = 20.0
VBXE_FRAC = {
    'scanline clip+addstep':              0.0,   # pure ZP fixed-point math
    'edge divide calc_step':              0.0,   # reciprocal LUT + multiply (RAM)
    'span row-offset calc (#3)':          0.0,   # row LUT in RAM
    'playlist read (pl_byte)':            0.3,   # (zp),y read hits VRAM; rest is CPU
    'poly fetch (poly_fetch)':            0.3,   # (zp),y read hits VRAM; rest is CPU
    'read_scaled coords (rs_fast/slow)':  0.15,  # only the (zp),y read is VRAM
    'poly ptr sync (setptr+get_dr_off)':  0.2,   # one bank write per sync
    'span BCB patch + fire (#1)':         0.7,   # BCB edits = MEMAC-A window writes
    'span blitter fire/wait (#1)':        0.9,   # BL_START + blit_idle poll = VBXE regs
    'copy/fill page blits':               0.9,   # blitter regs + wait
}


# --- VBXE blitter timing, from Altirra vbxe.cpp (authoritative) ---
#   blitter clock = 8x the CPU (mBlitCyclesLeft += 8*114 per scanline);
#   ~1 blitter-cycle per dest byte (mBlitCyclesPerRow = dstBytesPerRow, +src for copy);
#   21 blitter-cycles to load each BCB. So per span the blit takes
#   (21 + width[*2 copy]) blitter-cycles = /8 CPU-cycles.
BLIT_CLK_MULT = 8
BLIT_BCB_LOAD = 21


def blit_overlap_report(sim, cost, frames):
    print('\n' + '=' * 64)
    print(' BLIT-OVERLAP ANALYSIS  (real VBXE blitter timing from Altirra)')
    print('=' * 64)
    n = max(1, sim.n_span)
    avg_w = sim.span_bytes / n
    blit_cpu_per_span = (BLIT_BCB_LOAD + avg_w) / BLIT_CLK_MULT     # CPU-cyc equiv, solid
    # CPU work per span (edge walk + span emit), stock cycles:
    cpu_per_span = (C_DRAWSCAN + C_ADDSTEP + C_SPAN_OFFSET + C_SPAN_BCB + C_FIRE)
    print(f' avg span width        : {avg_w:.1f} B')
    print(f' blit time / span      : ~{blit_cpu_per_span:.1f} CPU-cyc  (21+W)/8')
    print(f' CPU work / span        : ~{cpu_per_span} stock-cyc  (edge+emit+BCB+fire)')
    print(f' blit / CPU ratio      : {100*blit_cpu_per_span/cpu_per_span:.0f}%'
          f'  -> blit is {"HIDDEN behind CPU work (pipelined)" if blit_cpu_per_span < cpu_per_span else "exposed"}')
    print(' => the per-span "blitter fire/wait" overhead the model assumed (C_BLIT_FIXED)')
    print('    is NOT real: the blit finishes during the next span\'s edge math, so the')
    print('    only real per-span blit cost is the BL_START write (C_FIRE) + a near-zero poll.')
    print(' => BCB-chaining removes that ~8-cyc fire BUT loses the per-span mode CACHE')
    print('    (each list slot needs its own AND/XOR/CTRL = +3 VBXE writes/span). On Rapidus')
    print('    (VBXE writes are native-speed) that ~cancels or exceeds the saving.')
    print(' VERDICT: BCB-chaining ~= break-even / slightly negative. NOT worth the risk.')
    print('          The real Rapidus cost is the per-span VBXE register WRITES (BCB DST+')
    print('          WIDTH+mode) + poly fetch -- chaining does not reduce those.')


def rapidus_report(cost, frames):
    print('\n' + '=' * 64)
    print(' RAPIDUS MODEL  (65C816 @ %.0f MHz from fast RAM ; VBXE/bus stays ~%.2f MHz)'
          % (RAPIDUS_CPU_MHZ, CPU_HZ / 1e6))
    print('=' * 64)
    print(f'   {"eff cyc":>12}   = vbxe(slow) + cpu/{RAPIDUS_CPU_MULT:.0f}      bucket')
    cpu_tot = vbxe_tot = eff_tot = 0.0
    for k, v in sorted(cost.items(), key=lambda kv: -kv[1]):
        f = VBXE_FRAC.get(k, 0.5)
        vbxe = v * f
        cpu = v * (1 - f)
        eff = vbxe + cpu / RAPIDUS_CPU_MULT
        cpu_tot += cpu; vbxe_tot += vbxe; eff_tot += eff
        print(f'   {eff:>12,.0f}   = {vbxe:>10,.0f} + {cpu/RAPIDUS_CPU_MULT:>8,.0f}   {k}')
    stock = sum(cost.values())
    print(f'\n   stock total    : {stock:>12,.0f} cyc   (~{CPU_HZ/(stock/frames):.1f} fps)')
    print(f'   VBXE/bus-bound : {vbxe_tot:>12,.0f} cyc   {100*vbxe_tot/stock:4.0f}% of stock -- does NOT accelerate')
    print(f'   CPU-bound      : {cpu_tot:>12,.0f} cyc   {100*cpu_tot/stock:4.0f}% -> /{RAPIDUS_CPU_MULT:.0f} on Rapidus')
    print(f'   Rapidus eff    : {eff_tot:>12,.0f} cyc   real speed-up {stock/eff_tot:.2f}x   (NOT {RAPIDUS_CPU_MULT:.0f}x!)')
    print(f'   Rapidus fps    : ~{CPU_HZ/(eff_tot/frames):.1f}'
          f'   ({(eff_tot/frames)/FRAME_CYC:.1f} vblanks/frame)')
    print('   NOTE: CPU mult (11x) + VBXE=native-speed are CONFIRMED from Altirra'
          ' (rapidus.cpp/vbxe.cpp); only the mixed-bucket VBXE_FRAC split is estimated.')

# --- approximate per-event 6502 cycle costs (from the asm sequences) ---
# 2026-06-10 fetch rework: pl_byte = running window ptr (no 24-bit addr recompute);
# poly_fetch = check-free + no dr_off upkeep (set_poly_ptr restores the bank,
# get_dr_off derives dr_off at the do_hier save); read_scaled = per-shape SMC
# dispatch (rs_fast inlines the fetch and skips mul_zoom when zoom==64 -- the
# old model did NOT count the read_scaled/mul_zoom overhead at all!).
C_PLBYTE_BANK = 9         # lda/cmp/beq, switch ~1/op (poly stole the bank)
C_PLBYTE_REST = 26        # ldy + (zp),y read + inc + jsr/rts
C_POLYBYTE = 26           # poly_fetch via jsr: ldy + read + inc + jsr/rts (no check)
C_RS_FAST = 37            # rs_fast TOTAL per coord: jsr+jmp+inline read+scaled+rts
C_RS_SLOW = 145           # rs_slow TOTAL per coord: jsr+jmp+poly_fetch+mul_zoom slow
C_SETPTR = 31             # set_poly_ptr: LUTs + unconditional bank write (per shape/child)
C_GETDROFF = 45           # get_dr_off + jsr (per do_hier child save)
C_SPAN_OFFSET = 24        # fill_span: row_lut[sy]+sx  (#3 DONE: was 170 via asl/rol)
C_SPAN_BCB = 34          # dst+width per span (width-1 straight from emit_span,
                          #   blit_idle+fire inlined); mode (AND/XOR/CTRL) cached
C_FIRE = 8               # BL_ADR loaded once at init; fire = just BL_START
C_BLIT_FIXED = 40         # blitter span: startup + busy-wait overhead (CPU side)
C_BLIT_PERBYTE = 1.0      # blitter fill ~ per dest byte (CPU-cycle equivalent of the wait)
C_CALCSTEP = 90         # reciprocal LUT + 8x16 multiply (was 360, 32-iter divide)
C_SLOPE_SETUP = 40        # calc_step sign/abs/setup
C_DRAWSCAN = 60           # draw_scanline: clip + min/max + signed compares
C_DRAWSCAN_FAST = 32      # draw_scanline_fast (bbox on-screen): order+copy only
C_ADDSTEP = 58           # inlined (no jsr/rts) in the row loop
C_CPUDRAW_SETUP = 30      # hypothetical CPU span: ptr setup
C_CPUDRAW_BYTE = 6        # hypothetical CPU span: sta (zp),y per byte
C_BIGBLIT = 60            # clear/copy page: one fire+wait (blitter does the bulk)


class Counter(sim_atari.Sim):
    def __init__(self):
        super().__init__()
        self.n_span = 0
        self.span_bytes = 0
        self.n_polybyte = 0
        self.n_plbyte = 0
        self.n_edge = 0
        self.n_scanline = 0
        self.n_copy = 0
        self.n_fill = 0
        self.n_drawpoly = 0
        self.n_blit = 0
        self.n_rs_fast = 0      # read_scaled coords, zoom == 64 (rs_fast path)
        self.n_rs_slow = 0      # read_scaled coords, zoom != 64 (rs_slow path)
        self.n_draw = 0         # poly_draw calls = drawpoly ops + hier children
        self.n_sl_fast = 0      # scanlines on the noclip path (bbox on-screen)
        self._inside = False
        self.span_hist = {}     # width bucket -> count

    def fill(self, off, color, zoom, ptx, pty):
        # peek the bbox WITHOUT self.by/self.mul (those would double-count)
        bbw = (self.poly[off & 0xFFFF] * zoom) >> 6
        bbh = (self.poly[(off + 1) & 0xFFFF] * zoom) >> 6
        x0 = ptx - bbw // 2
        y0 = pty - bbh // 2
        self._inside = (x0 >= 0 and x0 + bbw <= 319 and
                        y0 >= 0 and y0 + bbh <= 199)
        super().fill(off, color, zoom, ptx, pty)

    def by(self, off):
        self.n_polybyte += 1
        return super().by(off)

    def mul(self, m, zoom):
        if zoom == 64:
            self.n_rs_fast += 1
        else:
            self.n_rs_slow += 1
        return super().mul(m, zoom)

    def draw(self, off, x, y, zoom, col):
        self.n_draw += 1
        return super().draw(off, x, y, zoom, col)

    def u8(self):
        self.n_plbyte += 1
        return super().u8()

    def u16(self):
        self.n_plbyte += 2
        return super().u16()

    def calc_step(self, dv, hh):           # static in parent; count via wrapper below
        return sim_atari.Sim.calc_step(dv, hh)

    def fill_span(self, sx, sy, ln, color):
        self.n_span += 1
        self.span_bytes += ln
        b = min(160, ln)
        bucket = (b // 16) * 16
        self.span_hist[bucket] = self.span_hist.get(bucket, 0) + 1
        super().fill_span(sx, sy, ln, color)


def main():
    sim = Counter()
    # wrap calc_step + count scanlines/edges by patching the raster locals:
    # easiest: re-count edges + scanlines analytically during the run.
    orig_fpi = sim.fill_poly_int
    def fpi(pts, color):
        n = len(pts)
        if n >= 3:
            sim.n_edge += max(0, n - 2)             # ~one slope pair per segment*2
            ys = [p[1] for p in pts]
            sl = max(0, min(199, max(ys)) - max(0, min(ys)))
            sim.n_scanline += sl
            if sim._inside:
                sim.n_sl_fast += sl                 # noclip (draw_scanline_fast)
        orig_fpi(pts, color)
    sim.fill_poly_int = fpi

    # count opcodes
    pl = sim.pl
    # run, counting blit/copy/fill/drawpoly via a light re-scan of events:
    sim.run(10**9)
    # opcode counts from the playlist stream
    p = 0
    while p < len(pl):
        op = pl[p]; p += 1
        if op == 0: break
        if op == 1: p += 1
        elif op == 2: p += 1
        elif op == 3: sim.n_fill += 1; p += 2
        elif op == 4: sim.n_copy += 1; p += 2
        elif op == 5: sim.n_drawpoly += 1; p += 8
        elif op == 6: sim.n_blit += 1; p += 2
        elif op == 7: p += 5                    # DRAWTEXT: u16 strId + x + y + col
        elif op == 8: p += 1                    # SOUND: idx (else the stream desyncs)

    frames = sim.n_blit
    PAGE = 160 * 200

    # --- cost buckets (total cycles over the whole intro) ---
    children = sim.n_draw - sim.n_drawpoly      # do_hier recursion children
    n_rs = sim.n_rs_fast + sim.n_rs_slow        # coord reads via read_scaled
    n_pf = sim.n_polybyte - n_rs                # plain poly_fetch reads (byte0, counts...)
    cost = {}
    cost['playlist read (pl_byte)'] = sim.n_plbyte * (C_PLBYTE_BANK + C_PLBYTE_REST)
    cost['poly fetch (poly_fetch)'] = n_pf * C_POLYBYTE
    cost['read_scaled coords (rs_fast/slow)'] = (
        sim.n_rs_fast * C_RS_FAST + sim.n_rs_slow * C_RS_SLOW)
    cost['poly ptr sync (setptr+get_dr_off)'] = (
        (sim.n_drawpoly + 2 * children) * C_SETPTR + children * C_GETDROFF)
    cost['span row-offset calc (#3)'] = sim.n_span * C_SPAN_OFFSET
    cost['span BCB patch + fire (#1)'] = sim.n_span * (C_SPAN_BCB + C_FIRE)
    cost['span blitter fire/wait (#1)'] = int(
        sim.n_span * C_BLIT_FIXED + sim.span_bytes * C_BLIT_PERBYTE)
    cost['edge divide calc_step'] = sim.n_edge * (C_CALCSTEP + C_SLOPE_SETUP)
    cost['scanline clip+addstep'] = (
        sim.n_scanline * C_ADDSTEP +
        sim.n_sl_fast * C_DRAWSCAN_FAST +
        (sim.n_scanline - sim.n_sl_fast) * C_DRAWSCAN)
    cost['copy/fill page blits'] = (sim.n_copy + sim.n_fill) * C_BIGBLIT
    total = sum(cost.values())

    print('=' * 64)
    print(' PERF MODEL  (current renderer, PAL 6502 @ %.2f MHz)' % (CPU_HZ/1e6))
    print('=' * 64)
    print(f' frames(BLIT)    : {frames}')
    print(f' drawpoly        : {sim.n_drawpoly}')
    print(f' spans           : {sim.n_span:>9}   avg width {sim.span_bytes/max(1,sim.n_span):.1f} B')
    print(f' span bytes      : {sim.span_bytes:>9}')
    print(f' poly bytes read : {sim.n_polybyte:>9}')
    print(f' playlist bytes  : {sim.n_plbyte:>9}')
    print(f' edges (divides) : {sim.n_edge:>9}')
    print(f' scanlines drawn : {sim.n_scanline:>9}')
    print(f' copy/fill pages : {sim.n_copy}/{sim.n_fill}')
    print('\n span width histogram (LR bytes):')
    for k in sorted(sim.span_hist):
        print(f'   {k:3}-{k+15:3} : {sim.span_hist[k]}')

    print('\n cost breakdown (total cycles over the whole intro):')
    for k, v in sorted(cost.items(), key=lambda kv: -kv[1]):
        print(f'   {v:>13,}  {100*v/total:4.1f}%  {k}')
    print(f'   {total:>13,}  100.0%  TOTAL')

    sec = total / CPU_HZ
    print(f'\n estimated total render time : {sec:.1f} s for {frames} frames')
    print(f' estimated cycles / frame    : {total/frames:,.0f}'
          f'  ({total/frames/FRAME_CYC:.1f} vblanks/frame -> ~{CPU_HZ/(total/frames):.1f} fps)')

    # --- status of the numbered optimisations ---
    print('\n DONE: #2 bank cache -> superseded by the 2026-06-10 fetch rework (check-')
    print('   free poly_fetch + running pl ptr + rs_fast zoom dispatch). #3 row LUT,')
    print('   width-1 spans, inlined blit_idle/fire: all in. #1 (CPU draw) measured')
    print('   SLOWER (blitter wins). #4 (BCB-chaining) REJECTED -- see below.')
    print(f'   read_scaled split: {sim.n_rs_fast:,} fast (zoom==64) /'
          f' {sim.n_rs_slow:,} slow  ({100*sim.n_rs_fast/max(1,n_rs):.1f}% fast)')
    print(f'   poly_draw calls  : {sim.n_draw:,}  ({children:,} hier children)')

    blit_overlap_report(sim, cost, frames)
    rapidus_report(cost, frames)


if __name__ == '__main__':
    main()
