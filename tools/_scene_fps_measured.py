#!/usr/bin/env python3
"""
_scene_fps_measured.py - scene frame-rate estimate with the polygon cost MEASURED on a
6502 instead of summed statically from the listing (scene_fps.py's method).

Same scene driver as scene_fps.py (game_atari's faithful Python pipeline supplies the
draw calls, VM byte counts and page blits), but every TOP-LEVEL shape draw of the
measured window is ALSO executed by the real assembled code -- poly_draw of the OLD
baseline (_zal/base_20260909/awgame.xex) and of the NEW awgame.xex -- on the
cycle-counting 6502 core of _cpu6502.py, reading the part's real video1/video2 bytes
through an emulated MEMAC-B window. The two builds see the identical draw stream, so
the difference is purely the code change. The vm-fetch and page-blit buckets are taken
from scene_fps's model (unchanged code, identical in both builds).

Like scene_fps.py this ignores blitter waits and the shape-cell cache (both builds
share them; the cache would raise both numbers equally).

    python tools/_scene_fps_measured.py [part] [--frames N] [--full] [--cache]
        --cache = drive the scene through validate_cellcache's shape-cell cache model
        (what the real game does): hits cost their cell blits, everything else is
        rendered on the 6502.
        default: water 16002, 40 measured frames after the 30-frame prime, HALF detail
        (poly_bcb_h = 1 = what a stock 6502 runs); --full = full detail (Rapidus).
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scene_fps
import prof_tickcost as ptc
import validate_cellcache as vcc
import _cpu6502 as cpu6502
import _verify_skillpass as V
import _smcvars as SV                                             # noqa: E402

PROJ = os.path.dirname(HERE)
BL = 0xD653
MEMAC_B = 0xD65D
WIN = 0x4000


class Runner:
    """One build: image + persistent memory, executes poly_draw per top-level draw."""
    def __init__(self, xex, lst):
        self.b = V.Build(xex, lst)
        self.L = self.b.L
        self.mem = bytearray(self.b.img)
        self.cpu = cpu6502.CPU(self.mem)
        self.v1 = self.v2 = b''
        mem = self.mem
        def rd(a):
            if WIN <= a < 0x8000:
                bank = (mem[MEMAC_B] & 0x7F) - 0x14
                data = self.v1
                if bank >= 8:
                    bank -= 8; data = self.v2
                off = bank * 0x4000 + (a - WIN)
                return data[off] if off < len(data) else 0
            if a == BL:
                return 0
            return None
        self.cpu.rd_hook = rd
        L = self.L
        SV.poke(mem, L, 'hires', 0)
        mem[L['cc_baking']] = 0; mem[L['cc_flag']] = 0
        mem[L['last_scol']] = 0xFF
        mem[L['memb_cur']] = 0
        self.cycles = 0

    def draw(self, off, x, y, zoom, col, use2, half):
        L = self.L; mem = self.mem; cpu = self.cpu
        SV.poke(mem, L, 'poly_bcb_h', half)
        V.w16(mem, L['dr_off'], off & 0xFFFF)
        V.w16(mem, L['dr_x'], x & 0xFFFF); V.w16(mem, L['dr_y'], y & 0xFFFF)
        V.w16(mem, L['dr_zoom'], zoom & 0xFFFF)
        mem[L['dr_col']] = col & 0xFF
        mem[L['poly_base_adj']] = 8 if use2 else 0
        mem[L['psp']] = 0
        # do_draw's zoom dispatch (game_vm_draw.asm)
        z = zoom & 0xFFFF
        if z == 64:
            V.w16(mem, L['rs_smc'] + 1, L['rs_fast'])
        elif z < 16384:
            V.w16(mem, L['rs_smc'] + 1, L['rs_z4'])
            self.b.call(cpu, L['rs_z4_set'])
        else:
            V.w16(mem, L['rs_smc'] + 1, L['rs_slow'])
        cpu.cyc = 0
        self.b.call(cpu, L['set_poly_ptr'])      # do_draw does this before poly_draw
        self.b.call(cpu, L['poly_draw'])
        self.cycles += cpu.cyc


class MeasuredVM(scene_fps.Counter):
    """scene_fps's counting VM + the 6502 runs on every top-level draw (when armed)."""
    def __init__(self, part, runners, half):
        self.runners = runners; self.half = half; self.armed = False; self._depth = 0
        super().__init__(part)

    def draw(self, off, x, y, zoom, col):
        top = self._depth == 0
        if top and self.armed:
            use2 = self._pd is (self.poly2.d if self.poly2 else None)
            for r in self.runners:
                r.v1 = self.poly.d; r.v2 = self.poly2.d if self.poly2 else b''
                r.draw(off, x, y, zoom, col, use2, self.half)
        self._depth += 1
        try:
            return super().draw(off, x, y, zoom, col)
        finally:
            self._depth -= 1


class MeasuredCC(vcc.CellCacheVM):
    """validate_cellcache's 1:1 shape-cell-cache model + the 6502 runs on every top-level
    draw that the cache does NOT serve (misses, one-offs, NEVER shapes and the bakes --
    the real game renders a bake through poly_draw too). Cache hits cost the cell blits
    the model already prices (blit_cyc)."""
    def __init__(self, part, runners, half):
        self.runners = runners; self.half = half; self.armed = False
        self.copypg = 0
        super().__init__(part)

    def op_copypage(self):
        self.copypg += 1
        return super().op_copypage()


_orig_tc_draw = ptc.TickCost.draw
def _tc_draw(self, off, x, y, zoom, col):
    # CellCacheVM calls ptc.TickCost.draw explicitly (bypassing overrides) at depth 1 for
    # every top-level render (miss / seen / never / bake) and at depth > 1 for hier
    # children, which the 6502 poly_draw handles itself.
    if getattr(self, 'armed', False) and getattr(self, 'depth', 0) == 1:
        use2 = bool(getattr(self, 'use_video2', False) and self.poly2)
        for r in self.runners:
            r.v1 = self.poly.d; r.v2 = self.poly2.d if self.poly2 else b''
            r.draw(off, x, y, zoom, col, use2, self.half)
    return _orig_tc_draw(self, off, x, y, zoom, col)
ptc.TickCost.draw = _tc_draw


def measure_cc(part, pos, frames, half, runners):
    vm = MeasuredCC(part, runners, half)
    vm.var[0] = pos
    vm.input = scene_fps.GAMEPLAY_INPUT
    for _ in range(scene_fps.PRIME_FRAMES):          # warm the cache too
        if not vm.running or vm.next_part is not None:
            break
        vm.step()
    base_part = vm.cur_part
    f0 = len(vm.frames)
    c0 = dict(vm.c); s0 = dict(vm.stat); b0 = vm.blit_cyc; cp0 = vm.copypg
    for r in runners:
        r.cycles = 0
    vm.armed = True
    while len(vm.frames) - f0 < frames and vm.running:
        vm.step()
        if vm.cur_part != base_part:
            break
    n = max(1, len(vm.frames) - f0)
    holds = [vm.frames[i][2] for i in range(f0, len(vm.frames))]
    avg_hold = sum(holds) / len(holds) if holds else 1.0
    d = {key: (vm.c[key] - c0.get(key, 0)) / n for key in vm.c}
    st = {key: (vm.stat[key] - s0.get(key, 0)) / n for key in vm.stat}
    k = dict(vmbyte=d.get('code', 0), fillpage=d.get('fillpg', 0), copypage=(vm.copypg - cp0) / n,
             poly=d.get('draw', 0), span=d.get('span', 0), edge=d.get('edge', 0),
             blit=(vm.blit_cyc - b0) / n, hit=st.get('hit', 0), draws=sum(st.get(x, 0) for x in scene_fps._CC_DRAWS))
    return k, n, avg_hold, [r.cycles / n for r in runners]


def measure(part, pos, frames, half, runners):
    vm = MeasuredVM(part, runners, half)
    vm.var[0] = pos
    vm.input = scene_fps.GAMEPLAY_INPUT
    for _ in range(scene_fps.PRIME_FRAMES):
        if not vm.running or vm.next_part is not None:
            break
        vm.step()
    base_part = vm.cur_part
    f0 = len(vm.frames)
    vm.reset_counts()
    for r in runners:
        r.cycles = 0
    vm.armed = True
    while len(vm.frames) - f0 < frames and vm.running:
        vm.step()
        if vm.cur_part != base_part:
            break
    n = max(1, len(vm.frames) - f0)
    holds = [vm.frames[i][2] for i in range(f0, len(vm.frames))]
    avg_hold = sum(holds) / len(holds) if holds else 1.0
    k = {key[2:]: getattr(vm, key) / n for key in vars(vm) if key.startswith('k_')}
    return k, n, avg_hold, [r.cycles / n for r in runners]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    part = int(args[0]) if args else 16002
    frames = 40
    if '--frames' in sys.argv:
        frames = int(sys.argv[sys.argv.index('--frames') + 1])
    half = 0 if '--full' in sys.argv else 1
    name = next((nm for p, _, nm in scene_fps.SCENES if p == part), str(part))
    pos = next((ps for p, ps, _ in scene_fps.SCENES if p == part), 0)

    print('Assembling a fresh listing for the vm-fetch / page-blit costs ...')
    scene_fps.build_listing()
    # (scene_fps.Model itself is stale -- it still prices the long-gone pl_byte -- so take
    #  only the two unchanged page-blit procs + the mfetch constant from its Cost.)
    cost = scene_fps.Cost(scene_fps.parse_listing(scene_fps.LST))
    PAGE = 160 * 200
    class M:
        c_mfetch = 2 + 5 + 5 + 2
        c_fillpage = cost.proc('clear_page') + 6 + PAGE / 8.0
        c_copypage = cost.proc('copy_page') + 6 + 2 * PAGE / 8.0
    old = Runner(*V.OLD); new = Runner(*V.NEW)
    print('Running scene %s %d, %d measured frames, %s detail (6502 old + new per draw) ...'
          % (name, part, frames, 'HALF' if half else 'FULL'))
    cache = '--cache' in sys.argv
    if cache:
        k, n, hold, (cyc_old, cyc_new) = measure_cc(part, pos, frames, half, [old, new])
    else:
        k, n, hold, (cyc_old, cyc_new) = measure(part, pos, frames, half, [old, new])
    fixed = k['vmbyte'] * M.c_mfetch + k['fillpage'] * M.c_fillpage + k['copypage'] * M.c_copypage
    if cache:
        fixed += k['blit']
        print('   shape-cell cache ON: %.0f top-level draws/f, %.0f hits/f (%.0f%%), cell blits %.0f cyc/f'
              % (k['draws'], k['hit'], 100.0 * k['hit'] / max(1, k['draws']), k['blit']))
    tot_old = fixed + cyc_old; tot_new = fixed + cyc_new
    pace = scene_fps.VBLANK_HZ / hold if hold else scene_fps.VBLANK_HZ
    print()
    print('scene %s (%d): %d frames measured, %.0f polys/f, %.0f spans/f, %.0f edges/f'
          % (name, part, n, k['poly'], k['span'], k['edge']))
    print('   %-34s %12s %12s' % ('', 'OLD (pred)', 'NEW (teraz)'))
    print('   %-34s %12.0f %12.0f' % ('polygon draw cycles / frame (6502)', cyc_old, cyc_new))
    print('   %-34s %12.0f %12.0f' % ('vm fetch + page/cell blits / frame', fixed, fixed))
    print('   %-34s %12.0f %12.0f' % ('total cycles / frame', tot_old, tot_new))
    print('   %-34s %12.1f %12.1f' % ('fps (1.77 MHz PAL, render-bound)',
                                       scene_fps.fps_from_cycles(tot_old), scene_fps.fps_from_cycles(tot_new)))
    print('   %-34s %12.1f %12.1f' % ('scene pace (vblank/hold)', pace, pace))
    print('   polygon cycles: %.1f%% of old  (total: %.1f%%)'
          % (100.0 * cyc_new / cyc_old, 100.0 * tot_new / tot_old))
    if cache:
        print('   (no blitter waits on spans; cache hits priced as their cell blits; the scene')
        print('    runs at min(pace, fps).)')
    else:
        print('   (no blitter waits, no shape-cell cache -- same as scene_fps.py; the scene runs')
        print('    at min(pace, fps).)')


if __name__ == '__main__':
    main()
