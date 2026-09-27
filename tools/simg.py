#!/usr/bin/env python3
"""simg.py - run gr7/awgr7g.xex (the GR.7 GAME build) on the cycle-counting
6502 from sim.py and verify it against the game oracle (tools/game_sim.py).

What it checks, frame by frame (a frame = one op_updatedisplay):
  * the full 256-variable file (the strongest VM-correctness signal: one wrong
    fetch/branch/bank switch derails it within a few frames)
  * the shown page + the applied palette sequence
  * optionally renders side-by-side screenshots (xex vs the oracle reduced
    through the SAME per-part tables) -> gr7/out/gcmp.png / gsheet.png

What it measures: cycles per frame and where they go (profile by symbol),
with the pacing spin + vblank wait separated out -- the honest render cost.

    python gr7/tools/simg.py                 300 frames, trace + stats
    python gr7/tools/simg.py 600 --input 81  hero runs right (mask hex:
                                                1 R, 2 L, 4 D, 8 U, 80 fire)
    python gr7/tools/simg.py 300 --sheet 5,50,120,250
    python gr7/tools/simg.py 400 --nocmp --key 120:12:4 --key 200:1C:4
                                                press 'C' ($12) at frame 120 for
                                                4 frames, ESC ($1C) at 200; no
                                                oracle compare (part switches)
"""
import os
import re
import sys
import bisect
import collections
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
GR7 = os.path.dirname(HERE)
PROJ = os.path.dirname(GR7)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import atari_pal                    # noqa: F401  (pre-import for sim.shot)
import sim
import game_sim

sim.HERE = GR7                      # sim.py moved into tools/; the xex, the
sim.XEX = os.path.join(GR7, 'awgr7g.xex')       # listing and gr7.inc (DLIST
sim.LST = os.path.join(GR7, 'out', 'gr7g.lst')  # parse in shot()) live in gr7/
OUT = os.path.join(GR7, 'out')

FRAME_CYC = sim.FRAME_CYC
BUDGET = sim.BUDGET


def equates(path):
    out = {}
    for ln in open(path, encoding='utf-8'):
        m = re.match(r'^(\w+)\s+equ\s+\$([0-9A-Fa-f]+)', ln)
        if m:
            out[m.group(1)] = int(m.group(2), 16)
    return out


EQU = equates(os.path.join(GR7, 'gr7g.inc'))
VAR_LO, VAR_HI = EQU['var_lo'], EQU['var_hi']
VM_CUR2, VM_LASTPAL = EQU['vm_cur2'], EQU['vm_lastpal']
VM_RUNNING, HALTMARK = EQU['vm_running'], EQU['HALTMARK']
DLIST = EQU['DLIST']


class GMem(sim.Mem):
    """sim.Mem + the input registers the game reads (idle unless scripted)."""
    def __init__(self):
        super().__init__()
        self.joy = 0xFF             # PORTA bits, active low
        self.trig = 0x0F            # TRIG0 bit0 = 1 -> not pressed
        self.key = None             # keyboard code held right now (--key)

    def rd(self, a):
        a &= 0xFFFF
        if a == 0xD300:
            return self.joy
        if a == 0xD010:
            return self.trig
        if a == 0xD20F:             # SKSTAT bit2 = 0 while a key is held
            return 0xFB if self.key is not None else 0xFF
        if a == 0xD209:             # KBCODE: the scheduled key (or none)
            return self.key if self.key is not None else 0x3F
        if a == 0xD014:
            return 0x01             # PALNTSC: PAL machine (matches the PAL sim)
        return super().rd(a)

    def set_input(self, mask):
        """game_sim mask (1 R, 2 L, 4 D, 8 U, 0x80 fire) -> PORTA/TRIG0."""
        v = 0
        if mask & 1:
            v |= 0x08               # J_RIGHT
        if mask & 2:
            v |= 0x04
        if mask & 4:
            v |= 0x02
        if mask & 8:
            v |= 0x01
        self.joy = 0xFF ^ v
        self.trig = 0x0E if (mask & 0x80) else 0x0F


class TraceVM(game_sim.GameVM):
    """The oracle, with the milestone build's gaps mirrored (no text) and a
    per-frame record of (vars, shown page, shown palette)."""
    def __init__(self, part):
        super().__init__(part, engine='int')
        self.trace = []

    def op_drawstring(self):
        self.w(); self.b(); self.b(); self.b()

    def op_updatedisplay(self):
        import aw_sim
        aw_sim.VM.op_updatedisplay(self)
        self.trace.append((list(self.var), self.cur2, self.curpal & 0x1F))


TraceVM.OPS = list(game_sim.GameVM.OPS)
TraceVM.OPS[0x10] = TraceVM.op_updatedisplay
TraceVM.OPS[0x12] = TraceVM.op_drawstring


def active_dl(mem):
    """The display list on screen: SDLSTL/H (the flip writes the shadow too)."""
    return mem.base[0x0230] | (mem.base[0x0231] << 8)


def gshot(mem):
    """Decode what ANTIC would show from the ACTIVE display list: mode-D rows
    (2 scanlines), mode-4 text rows (8 scanlines through CHBAS) and blanks,
    coloured per DLI band from PALBND (band 0 = the OS shadows).
    -> (rgb 320 wide x one row per scanline, DL address, scanline count)"""
    import numpy as np
    from atari_pal import PALETTE
    dl = active_dl(mem)
    chbase = mem.base[0x02F4] << 8
    palbnd = list(mem.base[0x33B0:0x33B0 + 32])
    base = mem.base
    lines = []                      # (codes[160] or None, band)
    pc, lms, band = dl, 0, 0
    for _ in range(300):
        b = base[pc]; pc += 1
        mode = b & 0x0F
        if b == 0x41:
            break
        if mode == 0:
            for _k in range(((b >> 4) & 7) + 1):
                lines.append((None, band))
            continue
        if b & 0x40:
            lms = base[pc] | (base[pc + 1] << 8); pc += 2
        if mode == 0x0D:
            row = np.frombuffer(bytes(base[lms:lms + 40]), np.uint8)
            codes = np.zeros(160, np.uint8)
            for q in range(4):
                codes[q::4] = (row >> (6 - 2 * q)) & 3
            lines.append((codes, band)); lines.append((codes, band))
            lms += 40
        elif mode == 4:
            chars = base[lms:lms + 40]
            for j in range(8):
                cell = np.array([base[chbase + (c & 0x7F) * 8 + j] for c in chars],
                                np.uint8)
                codes = np.zeros(160, np.uint8)
                for q in range(4):
                    codes[q::4] = (cell >> (6 - 2 * q)) & 3
                lines.append((codes, band))
            lms += 40
        else:
            lines.append((None, band))
        if b & 0x80:
            band = min(band + 1, 7)
    rgb = np.zeros((len(lines), 320, 3), np.uint8)
    for i, (codes, bnd) in enumerate(lines):
        if codes is None:
            continue
        regs = np.array(palbnd[bnd * 4:bnd * 4 + 4], np.uint8)
        px = PALETTE[regs[codes]]
        rgb[i] = np.repeat(px, 2, 0)
    return rgb, dl, len(lines)


def main():
    args = sys.argv[1:]
    nframes = next((int(a) for a in args if a.isdigit()), 300)
    inmask = 0
    if '--input' in args:
        inmask = int(args[args.index('--input') + 1], 16)
    sheet = set()
    if '--sheet' in args:
        sheet = set(int(v) for v in args[args.index('--sheet') + 1].split(','))
    else:
        step = max(1, nframes // 6)
        sheet = set(range(2, nframes, step))
    nocmp = '--nocmp' in args
    keys = {}                       # frame -> (keycode, frames held)
    joys = {}                       # frame -> joystick mask from then on (--joy F:HEX)
    for i, a in enumerate(args):
        if a == '--key':
            f, code, *n = args[i + 1].split(':')
            keys[int(f)] = (int(code, 16), int(n[0]) if n else 4)
        if a == '--joy':
            f, m = args[i + 1].split(':')
            joys[int(f)] = int(m, 16)

    mem = GMem()
    cpu = sim.CPU(mem)
    mem.set_portb(0xFF)
    run = sim.load(mem, cpu)
    syms = sim.symbols()
    addrs = [a for a, _ in syms]
    names = [n for _, n in syms]
    by_name = {n: a for a, n in syms}
    print('RUN $%04X, banky s datami: %d' % (run, sum(1 for b in mem.ext if any(b))))

    part = None
    for ln in open(os.path.join(OUT, 'gp_data.inc'), encoding='ascii'):
        m = re.match(r'^GP_PART equ (\d+)', ln)
        if m:
            part = int(m.group(1))
    print('rezidentna cast: %d' % part)

    # ---- the oracle, same input schedule ----
    vm = TraceVM(part)
    vm.input = inmask
    vm.maxframes = nframes + 2
    while vm.running and len(vm.frames) < nframes + 2:
        vm.step()
        if vm.next_part is not None and vm.next_part != part:
            break
    oracle = vm.trace
    oframes = vm.frames
    print('oracle: %d snimok' % len(oracle))

    UPD = by_name.get('op_updatedisplay')
    WV = by_name.get('wait_vblank')
    if UPD is None or WV is None:
        raise SystemExit('op_updatedisplay/wait_vblank missing from the listing')
    mem.set_input(inmask)

    cpu.pc = run
    cpu._push(0x99)
    cpu._push(0x99)
    prof = collections.Counter()
    frames = []                     # (cycles, idle_pace, idle_wv) per frame
    grabs = []
    var_bad = None
    seq_bad = []
    shown_prev = None               # (lms_hi, lastpal, cur2) after flip k-1
    vbl = FRAME_CYC
    last = 0
    idle = collections.Counter()
    nfr = 0
    upd_end = next((a for a, _ in syms if a > UPD), 0x10000)
    wv_end = next((a for a, _ in syms if a > WV), 0x10000)
    key_left = 0
    shots = []                      # (frame, rgb) for --nocmp sheets
    # --- SIO: the loader's `jsr $E459` reads a sector off gr7/awgr7_full.atr ---
    atr_path = os.path.join(GR7, 'awgr7_full.atr')
    atr = open(atr_path, 'rb').read() if os.path.exists(atr_path) else None
    sio_reads = [0]
    def sio(cpu, mem):
        sec = mem.base[0x30A] | (mem.base[0x30B] << 8)
        buf = mem.base[0x304] | (mem.base[0x305] << 8)
        if atr is None:
            raise RuntimeError('SIO read but gr7/awgr7_full.atr is missing (build the disk first)')
        data = atr[16 + (sec - 1) * 128: 16 + sec * 128]
        for k, b in enumerate(data):
            mem.wr(buf + k, b)
        sio_reads[0] += 1
        cpu.y = 1; cpu.n = 0                     # status OK
        lo = mem.base[0x100 + ((cpu.sp + 1) & 0xFF)]
        hi = mem.base[0x100 + ((cpu.sp + 2) & 0xFF)]
        cpu.sp = (cpu.sp + 2) & 0xFF
        cpu.pc = ((hi << 8) | lo) + 1
    try:
        while nfr < (nframes if nocmp else min(nframes, len(oracle))):
            if cpu.pc == UPD:
                if nfr in joys:
                    mem.set_input(joys[nfr])
                if nfr in keys:
                    mem.key, key_left = keys[nfr]
                elif key_left > 0:
                    key_left -= 1
                    if key_left == 0:
                        mem.key = None
                if nocmp:
                    if nfr in sheet:
                        shots.append((nfr, gshot(mem)[0]))
                    frames.append((cpu.cyc - last, idle['pace'], idle['wv']))
                    last = cpu.cyc
                    idle.clear()
                    nfr += 1
                    pc0, c0 = cpu.pc, cpu.cyc
                    if cpu.pc == 0xE459:
                        sio(cpu, mem)
                        continue
                    cpu.step()
                    prof['op_updatedisplay(pace)'] += cpu.cyc - c0
                    if cpu.cyc >= vbl:
                        vbl += FRAME_CYC
                        mem.base[0x14] = (mem.base[0x14] + 1) & 0xFF
                    continue
                # frame boundary: vars now == the oracle's frame-nfr var file
                got = bytes(mem.base[VAR_LO:VAR_LO + 256]), \
                      bytes(mem.base[VAR_HI:VAR_HI + 256])
                want = oracle[nfr][0]
                if var_bad is None:
                    for i in range(256):
                        w = want[i] & 0xFFFF
                        if got[0][i] != (w & 0xFF) or got[1][i] != (w >> 8):
                            var_bad = (nfr, i, (got[1][i] << 8) | got[0][i], w)
                            break
                if shown_prev is not None and nfr >= 1:
                    o_cur2, o_pal = oracle[nfr - 1][1], oracle[nfr - 1][2]
                    lms, lp, c2 = shown_prev
                    exp_lms = 0x80 + o_cur2 * 0x10
                    if c2 != o_cur2 or (lms != exp_lms):
                        seq_bad.append((nfr - 1, 'page', c2, o_cur2))
                    if lp != 0xFF and lp != o_pal:
                        seq_bad.append((nfr - 1, 'pal', lp, o_pal))
                if nfr in sheet:
                    lms = mem.base[active_dl(mem) + 4] << 8   # page on screen now
                    grabs.append((nfr, bytes(mem.base[lms:lms + 4000]),
                                  mem.base[VM_LASTPAL]))
                frames.append((cpu.cyc - last, idle['pace'], idle['wv']))
                last = cpu.cyc
                idle.clear()
                nfr += 1
            if cpu.pc == 0xE459:
                sio(cpu, mem)
                continue
            pc0, c0 = cpu.pc, cpu.cyc
            cpu.step()
            dc = cpu.cyc - c0
            if UPD <= pc0 < upd_end:
                idle['pace'] += dc
                prof['op_updatedisplay(pace)'] += dc
            elif WV <= pc0 < wv_end:
                idle['wv'] += dc
                prof['wait_vblank'] += dc
            else:
                i = bisect.bisect_right(addrs, pc0) - 1
                if i >= 0:
                    prof[names[i]] += dc
            if cpu.pc == UPD and pc0 != UPD:
                pass
            if pc0 == UPD:          # entry consumed; remember flip state AFTER
                pass                #   the op via shown_prev refresh below
            shown_prev = (mem.base[active_dl(mem) + 4], mem.base[VM_LASTPAL],
                          mem.base[VM_CUR2])
            if cpu.cyc >= vbl:
                vbl += FRAME_CYC
                mem.base[0x14] = (mem.base[0x14] + 1) & 0xFF
            if mem.base[HALTMARK + 2] == 1:
                print('VM HALT: part switch na %d (frame %d)'
                      % (mem.base[HALTMARK] | (mem.base[HALTMARK + 1] << 8), nfr))
                break
    except RuntimeError as e:
        print('STOP:', e, '(po %d snimkach)' % nfr)

    print()
    print('SIO: %d sektorov precitanych z ATR' % sio_reads[0])
    if nocmp:
        print('(--nocmp: bez porovnania s oracle)')
    print('=== trace premennych proti oracle ====================')
    if nocmp:
        pass
    elif var_bad is None:
        print('  OK: vsetkych %d snimok x 256 premennych sedi' % nfr)
    else:
        f, i, g, w = var_bad
        print('  CHYBA: snimka %d, var $%02X: xex=$%04X oracle=$%04X'
              % (f, i, g, w & 0xFFFF))
    print('=== sekvencia stranka/paleta =========================')
    if nocmp:
        pass
    elif not seq_bad:
        print('  OK: shown page + applied palette sedia (%d snimok)' % max(0, nfr - 1))
    else:
        for f, kind, g, w in seq_bad[:8]:
            print('  CHYBA: snimka %d %s: xex=%d oracle=%d' % (f, kind, g, w))

    fr = frames[2:] or frames
    if fr:
        render = [c - p - w for c, p, w in fr]
        med = statistics.median(render)
        print()
        print('snimok %d   render-cyklov: min %d  median %.0f  max %d'
              % (len(fr), min(render), med, max(render)))
        heavy = sorted(range(len(render)), key=lambda i: -render[i])[:3]
        print('najtazsie snimky: ' + '  '.join(
            'f%d (%d cyk = %.1f PAL)' % (i + 2, render[i], render[i] / BUDGET)
            for i in heavy))
        print('PAL rozpocet s ANTIC D DMA: %d cyk/snimka' % BUDGET)
        print('median = %.2f PAL snimky -> render dovoli %.1f fps'
              % (med / BUDGET, 50 / max(0.02, med / BUDGET)))
        tot = sum(prof.values()) or 1
        print('kam ide cas (vratane cakania):')
        for n, c in prof.most_common(12):
            print('   %5.1f %%  %s' % (c * 100 / tot, n))

    # ---- screenshots + oracle comparison sheet ----
    # Three columns: the 16-colour AW render (what the main build displays),
    # the GR.7 target (oracle reduced through the SAME per-part tables), and
    # the actual xex frame.
    if grabs:
        import numpy as np
        from PIL import Image, ImageDraw
        from atari_pal import PALETTE
        lutb = open(os.path.join(OUT, f'gp{part}_lut16.bin'), 'rb').read()
        bandb = open(os.path.join(OUT, f'gp{part}_band.bin'), 'rb').read()
        bnd = np.frombuffer(bandb, np.uint8).reshape(32, 8, 4)   # DLI bands
        rowband = (np.arange(100) * 8) // 100    # row -> band
        awpal = np.array(vm.pals, np.uint8)      # 32 x 16 x RGB (the part's own)
        def band_render(codes, pal):
            """(100,160) 2-bit codes -> RGB through the palette's DLI bands,
            doubled to 320x200 -- what the machine actually shows."""
            regrow = bnd[pal][rowband]           # (100,4) registers per row
            px = PALETTE[np.take_along_axis(regrow, codes.astype(np.int64), 1)]
            return np.repeat(np.repeat(px, 2, 1), 2, 0)

        rows = []
        print()
        print('snimka   rozdiel voci oracle (cez rovnake tabulky)')
        for fn, pgbytes, lastpal in grabs:
            k = fn - 1              # the grab at entry k shows flip k-1
            if k < 0 or k >= len(oframes):
                continue
            page, pal, hold, draws, dl = oframes[k]
            pal &= 0x1F
            full = np.frombuffer(bytes(page), np.uint8).reshape(200, 320)
            a = full[::2, ::2]
            aw = np.repeat(awpal[pal][full[:, ::2] & 15], 2, 1)  # 160x200 -> x2
            codes = np.array([lutb[pal * 16 + (v & 15)] for v in range(16)],
                             np.uint8)[a & 15]
            ref = band_render(codes, pal)
            b = np.frombuffer(pgbytes, np.uint8).reshape(100, 40)
            xc = np.zeros((100, 160), np.uint8)  # unpack the xex's 2bpp page
            for q in range(4):
                xc[:, q::4] = (b >> (6 - 2 * q)) & 3
            got = band_render(xc, lastpal & 0x1F)
            h = min(ref.shape[0], got.shape[0])
            diff = (np.abs(ref[:h].astype(int) - got[:h].astype(int)).sum(2) > 0).mean() * 100
            print('  %5d   %5.1f %%   (paleta %d, %d draws)' % (k, diff, pal, draws))
            rows.append((k, aw, ref[:h], got[:h], diff))
        if rows:
            th, tw = rows[0][2].shape[:2]
            vh = rows[0][1].shape[0]
            rh = max(th, vh)
            im = Image.new('RGB', (3 * tw + 32, len(rows) * (rh + 22) + 8),
                           (24, 24, 28))
            dr = ImageDraw.Draw(im)
            for i, (fn, aw, r0, g0, df) in enumerate(rows):
                y = 8 + i * (rh + 22)
                im.paste(Image.fromarray(aw), (8, y))
                im.paste(Image.fromarray(r0), (tw + 16, y))
                im.paste(Image.fromarray(g0), (2 * tw + 24, y))
                dr.text((8, y + rh + 4), 'f%d  AW 16 farieb (hlavny build)' % fn,
                        (170, 200, 255))
                dr.text((tw + 16, y + rh + 4), 'ciel pre GR.7 (4 farby)',
                        (255, 210, 120))
                dr.text((2 * tw + 24, y + rh + 4), 'skutocny xex  %.1f %%' % df,
                        (150, 230, 170))
            im.save(os.path.join(OUT, 'gcmp.png'))
            print('-> gr7/out/gcmp.png')

    from PIL import Image, ImageDraw
    if shots:
        w = 320 + 16
        h = shots[0][1].shape[0] + 20
        cols = 2
        rows_n = (len(shots) + cols - 1) // cols
        im = Image.new('RGB', (cols * w + 8, rows_n * h + 8), (24, 24, 28))
        dr = ImageDraw.Draw(im)
        for i, (fn, rgb) in enumerate(shots):
            x = 8 + (i % cols) * w
            y = 8 + (i // cols) * h
            im.paste(Image.fromarray(rgb), (x, y))
            dr.text((x, y + rgb.shape[0] + 4), 'f%d' % fn, (170, 200, 255))
        im.save(os.path.join(OUT, 'gsheet.png'))
        print('-> gr7/out/gsheet.png  (%d snimok)' % len(shots))
    if '--dump' in args:
        b = mem.base
        pal = b[0xBA]
        print('DUMP: cur_pal %d lastpal %d cur2 %d dk_idx %d' % (pal, b[EQU['vm_lastpal']], b[VM_CUR2], b[EQU['dk_idx']]))
        print('  PALLUT[pal]', list(b[0x0E00 + pal * 16:0x0E00 + pal * 16 + 16]))
        print('  PALBND', ' '.join('%02X' % v for v in b[0x33B0:0x33B0 + 32]))
        lms = b[active_dl(mem) + 4] << 8
        pg = b[lms:lms + 4000]
        import collections as _c
        cnt = _c.Counter()
        for v in pg:
            for q in range(4): cnt[(v >> (6 - 2 * q)) & 3] += 1
        print('  shown page $%04X code histogram %s' % (lms, dict(cnt)))
        print('  bank10 lut slot3 @6A00', list(mem.ext[10][0x2A20:0x2A30]))
    a, dl, nl = gshot(mem)
    Image.fromarray(a).save(os.path.join(OUT, 'gshot.png'))
    print('-> gr7/out/gshot.png  (DL $%04X, %d skenliniek)' % (dl, nl))


if __name__ == '__main__':
    main()
