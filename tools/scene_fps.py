#!/usr/bin/env python3
"""
scene_fps.py - estimate the Atari frame rate of each PLAYABLE game scene.

The intro has perf_model.py; this is its game counterpart, with two differences the
project asked for:

  1. Only the PLAYABLE gameplay parts (water/jail/cite/luxe) -- a KNOWN list, not auto-
     detected (measuring proved unreliable: the intro cutscene reads the same joystick vars
     as water, and jail's hero is uncontrollable from a cold start; see the SCENES comment).
     arene/final/intro are cutscenes; protection/password are utility screens. Each scene is
     entered at its access-code CHECKPOINT (restartAt part,pos).

  2. The per-operation 6502 cycle costs are SUMMED STRAIGHT FROM THE ASSEMBLED GAME
     CODE -- i.e. from the build's listing (out/_scene_fps.lst). For each hot routine
     (poly_fetch, read_scaled, calc_step, draw_scanline, emit_span, fill_span, the
     fill_poly_int row/segment loops, set_poly_ptr, the page blits) we read the real
     opcode bytes mads emitted and add up their true 6502 cycle counts. Nothing is
     hand-guessed; change the asm and rebuild and these numbers move with it.

How the estimate works:
  * game_atari.GameAtari runs the SAME 6502 LR render pipeline the Atari runs (160-wide
    pages, the 320-space 16.16 edge walk, emit_span's x>>1). A counting subclass tallies
    the per-frame work (polygons, edges, spans/scanlines, poly bytes, coord scales, page
    blits, bytecode bytes) over a steady window of each scene.
  * per-frame cycles = sum over buckets of (count/frame * asm-derived cost/event).
  * fps(6502)   = PAL_CPU_HZ / cycles-per-frame.
  * pace(hold)  = the speed the DATA asks for (vblank rate / VAR_PAUSE_SLICES); the scene
    actually runs at min(pace, render-fps). A render fps below the pace = the scene is
    render-bound (the interesting case).

This is a MODEL (a static cycle count x a simulated op count): Altirra on real timing is
the ground truth. But the per-scene DIFFERENCES come from the op counts, which are exact
(game_atari runs the real bytecode), so the ranking and the ratios are solid.

    python tools/scene_fps.py            # all playable scenes
    python tools/scene_fps.py 16005      # just one part
"""
import os, sys, re, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import game_atari
import validate_cellcache as vcc          # the validated 1:1 shape-cell-cache model

ASM = os.path.join(PROJ, 'src_game', 'awgame.asm')
MADS = os.path.join(PROJ, 'mads.exe')
LST = os.path.join(PROJ, 'out', '_scene_fps.lst')        # fresh listing we build + parse
XEX = os.path.join(PROJ, 'out', '_scene_fps.xex')        # throwaway object

PAL_CPU_HZ = 1773447            # PAL 6502, ~1.77 MHz
FRAME_CYC = 35568               # cycles per PAL frame (312 lines * 114), ~49.86 Hz
VBLANK_HZ = PAL_CPU_HZ / FRAME_CYC

# Playable gameplay scenes only, each entered at a real access-code checkpoint (the
# lowest pos for that part from game_gui.CHECKPOINTS) so we land in gameplay, not the
# part's opening cut-scene. (part, VAR0 pos, label, code)
# PLAYABLE gameplay parts -- an authoritative KNOWN list, NOT auto-detected. Measuring it
# proved unreliable: the intro (16001, a cutscene) reads the EXACT same joystick vars as
# water (16002, gameplay) -- [0xE5,0xFA,0xFC,0xFE] -- so "does it read input?" can't tell
# them apart; and jail's hero is uncontrollable from a cold checkpoint, so "does the hero
# move?" wrongly calls it a cutscene. gameplay-vs-cutscene is game knowledge. NOT gameplay:
# 16001 intro, 16005 arene, 16007 final (cutscenes), 16000 protection, 16008 password.
SCENES = [
    (16002, 10, 'water'),
    (16003, 20, 'jail'),
    (16004, 30, 'cite'),
    (16006, 60, 'luxe'),
]

PRIME_FRAMES = 30               # skip the entry transition
MEAS_FRAMES = 150               # steady-state window to average over
# Active gameplay input (hero runs + acts), like validate_cellcache. The hero moving is
# the realistic case: anim cycles recur (drives the cache) and more shapes are on screen.
GAMEPLAY_INPUT = 0x01 | 0x80    # RIGHT + FIRE


# ===========================================================================
# 1) 6502 cycle counting straight from the assembled listing (out/_scene_fps.lst)
# ===========================================================================
MNEMONICS = set("""lda ldx ldy sta stx sty adc sbc and ora eor cmp cpx cpy bit
asl lsr rol ror inc dec inx iny dex dey tax txa tay tya tsx txs pha pla php plp
clc sec cli sei cld sed clv nop jmp jsr rts rti brk
bcc bcs beq bne bmi bpl bvc bvs""".split())
BRANCHES = {'bcc', 'bcs', 'beq', 'bne', 'bmi', 'bpl', 'bvc', 'bvs'}
RMW = {'asl', 'lsr', 'rol', 'ror', 'inc', 'dec'}
IMPLIED2 = {'inx', 'iny', 'dex', 'dey', 'tax', 'txa', 'tay', 'tya', 'tsx', 'txs',
            'clc', 'sec', 'cli', 'sei', 'cld', 'sed', 'clv', 'nop'}


def insn_cycles(mn, operand, nbytes):
    """Base NMOS 6502 cycles for one instruction. Branches counted not-taken (2);
    page-cross penalties ignored -- a steady straight-line per-call cost. nbytes (from
    the assembled bytes in the listing) disambiguates zero-page (2 B) from absolute (3)."""
    mn = mn.lower()
    operand = operand.strip()
    if mn in BRANCHES:
        return 2
    if mn == 'jsr':
        return 6
    if mn in ('rts', 'rti'):
        return 6
    if mn == 'brk':
        return 7
    if mn == 'jmp':
        return 5 if operand.startswith('(') else 3
    if mn in ('pha', 'php'):
        return 3
    if mn in ('pla', 'plp'):
        return 4
    if mn in IMPLIED2:
        return 2
    if mn in RMW:
        if operand in ('@', 'a', ''):
            return 2                                # accumulator
        if nbytes <= 2:
            return 6 if operand.endswith((',x', ',y')) else 5
        return 7 if operand.endswith((',x', ',y')) else 6
    # load / store / ALU group
    if operand.startswith('#'):
        return 2
    if operand.startswith('('):
        if ',x)' in operand:
            return 6                                # (zp,x)
        if '),y' in operand:
            return 6 if mn == 'sta' else 5          # (zp),y
        return 5
    idx = operand.endswith(',x') or operand.endswith(',y')
    if nbytes <= 2:                                 # zero page
        return 4 if idx else 3
    return (5 if mn == 'sta' else 4) if idx else 4  # absolute


_HEX2 = re.compile(r'^[0-9A-Fa-f]{2}$')
_HEX4 = re.compile(r'^[0-9A-Fa-f]{4}$')


def parse_listing(path):
    """Parse a mads .lst into {proc_name: [(src_line, cycles), ...]} for every .proc.
    Only lines that assembled to an instruction get cycles; labels/dirs/data get 0 but
    are kept so we can sum sub-ranges between local (?xxx) labels."""
    procs = {}
    cur = None
    with open(path, encoding='latin-1') as f:
        for raw in f:
            left, _, src = raw.rstrip('\n').partition('\t')
            src = src.strip()
            toks = left.split()
            # bytes emitted on this line: [lineno, addr4, bb, bb, ...] (skip "= addr" equ)
            nbytes = 0
            if len(toks) >= 2 and _HEX4.match(toks[1]):
                nbytes = sum(1 for t in toks[2:] if _HEX2.match(t))
            # .proc / .endp bracket the routine
            if src.startswith('.proc '):
                cur = src.split()[1]
                procs[cur] = []
                continue
            if src.startswith('.endp'):
                cur = None
                continue
            if cur is None or not src:
                continue
            # strip a leading label (?local or name) so the mnemonic is first
            parts = src.split()
            if parts and parts[0].lower() not in MNEMONICS and not parts[0].startswith(';'):
                parts = parts[1:]                   # drop the label
            cyc = 0
            if parts and parts[0].lower() in MNEMONICS and nbytes:
                mn = parts[0]
                operand = parts[1] if len(parts) > 1 and not parts[1].startswith(';') else ''
                cyc = insn_cycles(mn, operand, nbytes)
            procs[cur].append((src, cyc))
    return procs


class Cost:
    """Sum cycles of a routine (or a sub-range between two local labels) from the lst."""
    def __init__(self, procs):
        self.procs = procs

    def proc(self, name):
        if name not in self.procs:
            raise KeyError(f'routine {name!r} not in the listing (build changed?)')
        return sum(c for _, c in self.procs[name])

    def span(self, name, start, end):
        """Cycles from the line whose first token == `start` up to (excluding) `end`."""
        body = self.procs[name]
        i0 = next(i for i, (s, _) in enumerate(body) if s.split()[:1] == [start])
        i1 = next(i for i, (s, _) in enumerate(body[i0:], i0) if s.split()[:1] == [end])
        return sum(c for _, c in body[i0:i1])

    def exclude(self, name, *ranges):
        """Whole routine MINUS the given [start,end) label ranges -- used to drop the
        dead branches gameplay never takes (e.g. emit_span's SR block, fill_span's
        copy/transparent blocks), so the cost is the real LR/solid-span path."""
        return self.proc(name) - sum(self.span(name, a, b) for a, b in ranges)


def build_listing():
    """Assemble a fresh listing so the cycle costs reflect the CURRENT asm (one pass is
    enough -- routine bodies don't depend on the 2-pass ATR sector table)."""
    if not os.path.exists(MADS):
        if os.path.exists(LST):
            print(f'[warn] mads.exe not found; using existing {LST} (may be stale)')
            return
        raise SystemExit('mads.exe not found and no listing to fall back on')
    os.makedirs(os.path.dirname(LST), exist_ok=True)
    r = subprocess.run([MADS, ASM, f'-o:{XEX}', f'-l:{LST}'],
                       cwd=PROJ, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(LST):
        print(r.stdout, r.stderr)
        raise SystemExit('assembling the listing failed')


# ===========================================================================
# 2) per-event cost model, every constant summed from the game .lst above
# ===========================================================================
class Model:
    def __init__(self, cost):
        c = cost
        JSR = 6
        # --- leaf routines (called per event): body cycles + the jsr that reached them
        self.c_polyfetch = c.proc('poly_fetch') + JSR          # per plain poly byte
        self.c_rs_fast = c.proc('rs_fast') + JSR               # per coord, zoom == 64
        self.c_rs_z4 = c.proc('rs_z4') + JSR                   # per coord, zoom != 64
        self.c_setptr = c.proc('set_poly_ptr') + JSR           # per shape + per hier child
        self.c_plbyte = c.proc('pl_byte') + JSR                # opcode-byte fetch
        # --- polygon raster, decomposed out of fill_poly_int with no double counting:
        self.c_polyhead = c.span('fill_poly_int', 'lda', '?seg')   # per polygon: vertex/edge setup
        # fill_poly_int's ?seg loop runs once per SEGMENT (= 2 edges: it advances the
        # left + right edge together, calling calc_step twice). With n_edge = Σ(nverts-2)
        # = the number of calc_step CALLS, there are n_edge/2 segments. c_segment is one
        # segment iteration's setup (h/dvr/dvl/hh + the 2 `jsr calc_step`, bodies separate).
        self.c_segment = c.span('fill_poly_int', '?cont', '?row')
        self.c_calcstep = c.proc('calc_step')                  # one call's body (jsr is in c_segment)
        # per drawn scanline: the fill_poly_int row-loop body (add-steps + the jsr) + the
        # routines it tail-calls. Use the GAMEPLAY path of each: draw_scanline_FAST (bbox
        # on-screen, the common dispatch), emit_span's LR branch (gameplay is LR 160, not
        # the SR-320 access-code mode), and fill_span's SOLID branch (poly spans are a
        # solid colour) -- the copy/transparent/SR blocks are dead here, so drop them.
        self.c_rowloop = c.span('fill_poly_int', '?row', '?segnext')
        c_emit_lr = c.exclude('emit_span', ('?sr', '?col'))
        c_fill_solid = c.exclude('fill_span', ('?copy', '?transp'), ('?transp', '?solid'))
        self.c_scanline = (self.c_rowloop + c.proc('draw_scanline_fast')
                           + c_emit_lr + c_fill_solid)
        # --- big page blits: the routine + the blitter run (VBXE, 8x CPU clock, ~1
        #     byte/cycle; copy reads src+dst). One LR page = 160*200 dst bytes.
        PAGE = 160 * 200
        self.c_fillpage = c.proc('clear_page') + JSR + PAGE / 8.0
        self.c_copypage = c.proc('copy_page') + JSR + 2 * PAGE / 8.0
        # mfetch operand byte (inline macro, no jsr): ldy#0 + lda(zp),y + inc zp + branch
        self.c_mfetch = 2 + 5 + 5 + 2


# ===========================================================================
# 3) counting render run of one scene through the faithful Atari LR pipeline
# ===========================================================================
class Counter(game_atari.GameAtari):
    """game_atari + per-frame op tallies (same hook points perf_model uses on the intro)."""
    def __init__(self, *a, **kw):
        self.reset_counts()                  # counters must exist before load_part fetches
        super().__init__(*a, **kw)

    def reset_counts(self):
        self.k_vmbyte = self.k_polybyte = self.k_coord = self.k_rsfast = 0
        self.k_draw = self.k_poly = self.k_edge = self.k_span = self.k_spanbytes = 0
        self.k_fillpage = self.k_copypage = 0

    def b(self):
        self.k_vmbyte += 1
        return super().b()

    def w(self):
        self.k_vmbyte += 2
        return super().w()

    def by(self, off):
        self.k_polybyte += 1
        return super().by(off)

    def mul(self, m, zoom):
        self.k_coord += 1
        if zoom == 64:
            self.k_rsfast += 1
        return super().mul(m, zoom)

    def draw(self, off, x, y, zoom, col):
        self.k_draw += 1
        return super().draw(off, x, y, zoom, col)

    def fill_poly_int(self, pts, color):
        self.k_poly += 1
        self.k_edge += max(0, len(pts) - 2)
        return super().fill_poly_int(pts, color)

    def fill_span(self, sx, sy, ln, color):
        self.k_span += 1
        self.k_spanbytes += ln
        return super().fill_span(sx, sy, ln, color)

    def op_fillpage(self):
        self.k_fillpage += 1
        return super().op_fillpage()

    def op_copypage(self):
        self.k_copypage += 1
        return super().op_copypage()


def measure_scene(part, pos):
    """Run one scene, return (per-frame averages dict, n_frames, avg_hold, switched)."""
    vm = Counter(part)
    vm.var[0] = pos                      # AW restartAt(part, pos): prime VAR(0)
    vm.input = GAMEPLAY_INPUT            # active gameplay (hero runs + acts)
    for _ in range(PRIME_FRAMES):        # skip the opening transition
        if not vm.running or vm.next_part is not None:
            break
        vm.step()
    base_part = vm.cur_part
    f0 = len(vm.frames)
    vm.reset_counts()
    switched = False
    while len(vm.frames) - f0 < MEAS_FRAMES and vm.running:
        vm.step()
        if vm.cur_part != base_part:     # crossed into another part -> stop at the boundary
            switched = True
            break
    n = max(1, len(vm.frames) - f0)
    holds = [vm.frames[i][2] for i in range(f0, len(vm.frames))]
    avg_hold = sum(holds) / len(holds) if holds else 1.0
    k = {key[2:]: getattr(vm, key) / n for key in vars(vm) if key.startswith('k_')}
    return k, len(vm.frames) - f0, avg_hold, switched


# stat keys validate_cellcache tallies per TOP-LEVEL draw (hier children render normally)
_CC_DRAWS = ('hit', 'bake', 'special', 'never_draw', 'toobig', 'edge', 'nofit', 'seen')


def measure_cache(part, pos):
    """Run the same scene through validate_cellcache's CellCacheVM (the validated 1:1
    model of the game's VRAM shape-cell cache) and return its steady-state cache stats:
    (hit%, cacheable%, draws, arena_full). hit = a recurring shape blitted from a baked
    cell (skips decode+raster entirely); cacheable = hit OR bake (shapes the cache can
    hold); the rest are one-offs (seen) or ineligible (never/edge/toobig/nofit). Measured
    over the same checkpoint + window + active input as the cost columns."""
    try:
        vm = vcc.CellCacheVM(part)
    except Exception:
        return None
    vm.var[0] = pos
    vm.input = GAMEPLAY_INPUT
    for _ in range(PRIME_FRAMES):                 # warm the cache (SEEN -> bake -> CELL)
        if not vm.running or vm.next_part is not None:
            break
        vm.step()
    base = dict(vm.stat)                          # snapshot, then diff over the window
    base_part = vm.cur_part
    f0 = len(vm.frames)
    while len(vm.frames) - f0 < MEAS_FRAMES and vm.running:
        vm.step()
        if vm.cur_part != base_part:
            break
    d = {key: vm.stat[key] - base.get(key, 0) for key in vm.stat}
    draws = sum(d.get(key, 0) for key in _CC_DRAWS)
    hit = d.get('hit', 0)
    cacheable = hit + d.get('bake', 0)
    arena_full = d.get('nofit', 0) > 0           # cache ran out of VRAM holes for this part
    if draws <= 0:
        return None
    return 100 * hit / draws, 100 * cacheable / draws, draws, arena_full


# ===========================================================================
# 4) cycles/frame from the counts x the asm costs, then fps
# ===========================================================================
def frame_buckets(k, M):
    """Per-frame cycles split into the named buckets (k = per-frame op counts)."""
    n_pf = max(0.0, k['polybyte'] - k['coord'])          # plain poly bytes (not coord reads)
    n_rsz4 = max(0.0, k['coord'] - k['rsfast'])
    children = max(0.0, k['draw'] - k['poly'])           # do_hier children (extra set_poly_ptr)
    op_bytes = k['vmbyte']
    return {
        'vm fetch':    op_bytes * M.c_mfetch,            # bytecode byte stream (mfetch-dominated)
        'poly fetch':  n_pf * M.c_polyfetch,
        'coord scale': k['rsfast'] * M.c_rs_fast + n_rsz4 * M.c_rs_z4,
        'shape ptr':   (k['poly'] + children) * M.c_setptr,
        'poly setup':  k['poly'] * M.c_polyhead,
        # n_edge calc_step calls + n_edge/2 segment iterations (2 edges per segment)
        'edges':       k['edge'] * M.c_calcstep + (k['edge'] / 2) * M.c_segment,
        'scanlines':   k['span'] * M.c_scanline,
        'page blits':  k['fillpage'] * M.c_fillpage + k['copypage'] * M.c_copypage,
    }


def fps_from_cycles(cyc):
    return PAL_CPU_HZ / cyc if cyc > 0 else 0.0


def main():
    only = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print('Assembling a fresh listing for the cycle costs ...')
    build_listing()
    cost = Cost(parse_listing(LST))
    M = Model(cost)

    print('\n' + '=' * 78)
    print(' PER-EVENT 6502 COSTS  (cycles summed straight from out/_scene_fps.lst)')
    print('=' * 78)
    rows = [
        ('poly_fetch (per poly byte)', M.c_polyfetch),
        ('rs_fast    (per coord, zoom 1:1)', M.c_rs_fast),
        ('rs_z4      (per coord, zoomed)', M.c_rs_z4),
        ('set_poly_ptr (per shape/child)', M.c_setptr),
        ('calc_step  (per edge call)', M.c_calcstep),
        ('fill_poly_int head (per polygon)', M.c_polyhead),
        ('edge segment (per 2 edges, +2 calc_step)', M.c_segment),
        ('scanline (row-loop+draw+emit+fill_span)', M.c_scanline),
        ('clear_page (+ blit)', M.c_fillpage),
        ('copy_page  (+ blit)', M.c_copypage),
    ]
    for label, v in rows:
        print(f'   {v:8.0f} cyc   {label}')

    print('\n' + '=' * 78)
    print(' PLAYABLE SCENE FRAME RATE  (model: asm cycles x game_atari op-counts)')
    print('=' * 78)
    print(f'   PAL 6502 @ {PAL_CPU_HZ/1e6:.2f} MHz, vblank {VBLANK_HZ:.1f} Hz; '
          f'active input (hero runs + fires)')
    hdr = (f'\n   {"scene":<14}{"polys/f":>8}{"spans/f":>8}{"edges/f":>8}'
           f'{"cyc/f":>10}{"fps":>8}{"pace":>8}{"cache-hit":>11}{"cacheable":>11}')
    print(hdr)
    print('   ' + '-' * (len(hdr) - 4))
    for part, pos, name in SCENES:
        if only is not None and part != only:
            continue
        k, nfr, hold, switched = measure_scene(part, pos)
        buckets = frame_buckets(k, M)
        cyc = sum(buckets.values())
        fps = fps_from_cycles(cyc)
        pace = VBLANK_HZ / hold if hold else VBLANK_HZ
        cc = measure_cache(part, pos)             # (hit%, cacheable%, draws, arena_full)
        if cc is None:
            cchit, ccable = '   n/a', '   n/a'
        else:
            hpct, apct, _, full = cc
            cchit = f'{hpct:8.0f}%' + ('!' if full else ' ')
            ccable = f'{apct:9.0f}%'
        flag = '*' if (switched or nfr < 20) else ' '
        label = f'{name} {part}{flag}'
        print(f'   {label:<14}{k["poly"]:>8.0f}{k["span"]:>8.0f}{k["edge"]:>8.0f}'
              f'{cyc:>10.0f}{fps:>8.1f}{pace:>8.1f}{cchit:>11}{ccable:>11}')
    print('\n   pace = the fps the scene DATA asks for (vblank/hold); the scene runs at')
    print('   min(pace, fps). fps below pace = render-bound.  * = few frames / part')
    print('   switched during the window (treat as indicative).')
    print('   cache-hit = % of top-level shape draws served from a baked VRAM cell (skip')
    print('   the whole raster); cacheable = hit + bakeable (the cache\'s reachable ceiling);')
    print('   the gap to 100% is one-offs + ineligible shapes. "!" = cache ran out of VRAM')
    print('   holes for that part (validate_cellcache PART_HOLES is unfilled for cite/luxe/')
    print('   final, so their cacheable is a floor). Cache model = tools/validate_cellcache.py.')
    print('\n   NOTE: a cost MODEL -- cycles are real (from the assembled game), op counts')
    print('   are exact (game_atari runs the bytecode), but branch/page-cross penalties')
    print('   and blit overlap are approximated. Altirra on real timing is the truth.')


if __name__ == '__main__':
    main()
