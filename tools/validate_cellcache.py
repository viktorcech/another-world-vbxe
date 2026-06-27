#!/usr/bin/env python3
"""
validate_cellcache.py - Python model of the GAME's VRAM shape-cell cache,
bit-exact-validated against the uncached engine.

The trick: a top-level polygon draw recurs 70-83% of the time (hero anim cycle,
scenery, the scrolling water surface). Rasterise it ONCE into cells in free
VRAM and replay every recurrence as a few blits instead of decoding +
rasterising thousands of vertices per tick.

Mechanics mirrored 1:1 from the planned 6502 implementation:

* KEY = (off, zoom, x&1, video-bank). The LR floor(x/2) byte mapping shifts
  content exactly only on EVEN x deltas -> a cell is valid only at the x parity
  it was baked at. The same off in video1 vs video2 is a different shape.
* INDEX: 512-entry direct-mapped table (8 KB, lives in VRAM via MEMAC-B; a
  48-entry RAM index thrashes -- measured). Collision = evict entry.
* SEEN filter: the FIRST encounter of a key only marks it SEEN; the bake
  happens on the SECOND (recurrence proven). One-off cinematic shapes would
  otherwise flood the arena (water: 171 KB of one-offs).
* ARENAS: per-part list of free VRAM holes (two 32 KB page-upper holes + the
  v1/code/v2 region remainders; SFX banks excluded). Bump allocation; if a
  cell doesn't fit -> THAT key goes NEVER, others keep allocating. Cells are
  stored HALF-RES (the stock 6502 renders 2-tall spans anyway): half the
  bytes, blitted twice (y, y+1).
* BAKE at screen centre (translation-invariant), on a blank scratch page with
  value semantics that replay the painter's order per byte:
      solid colour c -> byte = c+$10      ($10-$1F; colour 0 stays nonzero)
      0x10 (dest|=8) -> byte |= $08       (empty->$08, solid->composed const,
                                           p0-mark $20 -> $28)
      0x11+ (copy page0) -> byte = $20    (erases earlier writers, like the
                                           real fill does)
  Final byte classes: 0 empty / $08 or-only / $10-$1F solid const / $20 p0 /
  $28 p0-then-or. MIXED groups are fine -- on the 6502 the classes are
  separated by per-class MASK RE-RENDERS (a class renders $FF, an erasing
  class renders $00 -> the mask replays the overwrite order; solid-value
  pollution from 0x10-on-empty is killed by one AND blit with the solid mask),
  so no per-byte CPU extraction is needed. The model burns the same number of
  extra renders to keep the cost honest.
* REUSE = up to 3 blit groups in the fixed order p0 -> solid -> or:
      p0   : 4-blit chain (page0 rect -> scratch, AND p0-mask, ADD $10 via the
             same mask with BCB_AND=$10, BSTENCIL scratch -> page) -- reads
             page0 LIVE, so an animated background stays correct
      solid: BSTENCIL, BCB_AND=$0F (strips the +$10, skips empty)
      or   : BLT_OR of $08 bytes (idempotent on composed solids)
  Correct for every class interleaving except 0x10-before-0x11 on the same
  byte, which the $20 erase handles, and the stitch check below.
* WIDE shapes: group headers LIE about their bbox (water surface header says
  2x128, content is ~370 px wide), so wideness is detected from the BAKED
  extents: touching the left/right canvas edge only -> re-bake as TWO stitched
  strips (centre 318+p then p; even deltas keep the byte grid aligned, the
  overlap byte must agree) -> position-free cells up to 636 px wide.
  Extents touching top/bottom -> POSKEY: position-keyed cells (exact clipped
  content at that fixed position). Still-touching wide bakes -> NEVER.

Validation: run each part in lockstep vs the uncached reference with the stick
held; every frame must be byte-identical. The cost model (perf_model
constants) then reports the new tick time vs the hold.

  python tools/validate_cellcache.py            # water/arene/jail, 250 frames
  python tools/validate_cellcache.py 16002 400
"""
import os, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_atari, sim_atari, prof_tickcost as ptc

LW, H = 160, 200
FRAME = ptc.FRAME_CYC
INDEX_N = 512                 # direct-mapped index entries (8 KB in VRAM, MEMAC-B)
# STAGE1=1 : model only the first 6502 port stage -- pure-solid narrow cells
# (probe-clean bakes only), page arenas only (no per-part holes, -8KB index)
STAGE1 = os.environ.get('STAGE1') == '1'
CELL_MAX = 49152              # full-res w*h cap (stored half-res = half of this);
                              # big recurring overlays (water surface 319x128) must fit
C_BLIT_SETUP = 120            # BCB patch + idle + fire per cell blit
INPUT = 0x01 | 0x80           # RIGHT + FIRE (hero runs)

# arena layout per part: the two free page-upper holes (page 2 upper = scratch)
# + the per-part v1/code/v2 region holes (sizes from game_atr.inc; the 6502 gets
# these from a generated table). SFX banks $0E/$0F/$11-$13/$1E/$1F excluded.
PAGE_ARENAS = [32768, 32768]
PART_HOLES = {                # v1 hole, code hole, v2 hole (bytes)
    16002: [8026, 44726, 7660],     # water
    16003: [11776, 25728, 7552],    # jail
    16004: [0, 0, 0],               # cite (not profiled; page arenas only)
    16005: [36224, 57216, 32768],   # arene (no v2 -> whole $070000-$077FFF)
    16006: [0, 0, 0],
    16007: [0, 0, 0],
}


class CellCacheVM(ptc.TickCost):
    def __init__(self, part):
        super().__init__(part)
        self.depth = 0
        self.scratch = bytearray(LW * H)
        self.index = [None] * INDEX_N      # entry: dict(key, state, cell...)
        self.index2 = [None] * INDEX_N     # position-keyed cells (vertical overflow)
        if STAGE1:
            self.arenas = [32768, 24576]       # page-0 upper + page-1 upper less index
        else:
            self.arenas = PAGE_ARENAS \
                + [s for s in PART_HOLES.get(part, [0, 0, 0]) if s]
        self.bump = [0] * len(self.arenas)
        self.stat = collections.Counter()
        self.baking = False
        self.bb = None
        self.scratch_w = LW
        self.blit_cyc = 0.0                # per-tick blit cost (added to model)

    # ---- cache plumbing -------------------------------------------------
    @staticmethod
    def hslot(key):
        off, zoom, par, bank = key
        return (off ^ (zoom * 7) ^ (par * 97) ^ (bank * 41)) % INDEX_N

    @staticmethod
    def hslot2(key):
        off, zoom, bank, x, y = key
        return (off ^ (zoom * 7) ^ (bank * 41) ^ (x * 13) ^ (y * 29)) % INDEX_N

    def alloc(self, size):
        # FULL-RES cells: the half-res device render uses per-poly RELATIVE row
        # parity, so adjacent rows are NOT duplicates -- cells must store every
        # row. A mixed cell needs one sub-cell per class (solid value / or mask
        # / p0 mask), all sharing the bbox -> the caller multiplies the size.
        for a in range(len(self.arenas)):
            if self.bump[a] + size <= self.arenas[a]:
                self.bump[a] += size
                return True
        return False                            # caller marks THIS key NEVER

    # ---- bake-mode span semantics (see the header) ------------------------
    def fill_span(self, sx, sy, ln, color):
        if not self.baking:
            return ptc.TickCost.fill_span(self, sx, sy, ln, color)
        self.c['span'] += 1                     # keep the cost counters honest
        self.c['spanb'] += ln
        ln = max(1, ln)
        x1 = sx + ln - 1
        if self.bb is None:
            self.bb = [sx, sy, x1, sy]
        else:
            b = self.bb
            b[0] = min(b[0], sx); b[1] = min(b[1], sy)
            b[2] = max(b[2], x1); b[3] = max(b[3], sy)
        off = sy * LW + sx
        if color == 0x10:
            for k in range(ln):
                self.scratch[off + k] |= 0x08
        elif color >= 0x11:
            for k in range(ln):
                self.scratch[off + k] = 0x20
        else:
            # solid bake byte = c|$F0 (never 0). The VBXE stencil tests the
            # POST-AND/XOR value AND writes that same value (Altirra vbxe.cpp),
            # so colour 0 cannot be written by a stencil blit at all; the
            # replay is a 2-blit pair instead: BLT_AND (AND=$F0: dest &= $F0
            # -> 0, page bytes are 0-15) then BLT_OR (AND=$0F: dest |= c;
            # colour 0 skips, dest is already 0). $10-composition still works:
            # (c|$F0)|8 stays in $F0-$FF.
            v = color | 0xF0
            for k in range(ln):
                self.scratch[off + k] = v

    def fill_poly_int(self, pts, color):
        if self.baking and len(pts) < 3:
            # the 6502 dot plot skips x >= 256 (position-dependent), so any
            # shape containing dots is uncacheable on the device
            self.kinds.add('dots')
        return ptc.TickCost.fill_poly_int(self, pts, color)

    def fill(self, off, color, zoom, ptx, pty):
        if self.baking:
            # per-FILL clip guard (mirrors the do_fill dispatch margin on the
            # 6502): a child fully off-screen at the bake position emits no
            # spans, so extents alone cannot detect the loss -- any fill whose
            # bbox is not fully on-screen makes the bake untrustworthy.
            bbw = (self._pd[off & 0xFFFF] * zoom) >> 6
            bbh = (self._pd[(off + 1) & 0xFFFF] * zoom) >> 6
            x0 = ptx - (bbw >> 1)
            y0 = pty - (bbh >> 1)
            if not (x0 >= 1 and x0 + bbw <= 318 and 0 <= y0 and y0 + bbh <= 199):
                self.kinds.add('clipped')
        return ptc.TickCost.fill(self, off, color, zoom, ptx, pty)

    # ---- the cached top-level draw ---------------------------------------
    def draw(self, off, x, y, zoom, col):
        self.depth += 1
        if self.depth > 1:                      # hier child: normal path
            r = ptc.TickCost.draw(self, off, x, y, zoom, col)
            self.depth -= 1
            return r

        bank = 1 if getattr(self, 'use_video2', False) and self.poly2 else 0
        key = (off, zoom, x & 1, bank)
        slot = self.hslot(key)
        e = self.index[slot]

        poskey = None
        if e is not None and e['key'] == key:
            if e['state'] == 'CELL':
                self._blit_cell(e, x, y)        # HIT
                self.stat['hit'] += 1
                self.depth -= 1
                return
            if e['state'] == 'NEVER':
                r = ptc.TickCost.draw(self, off, x, y, zoom, col)
                self.stat['never_draw'] += 1
                self.depth -= 1
                return r
            if e['state'] == 'POSKEY':
                poskey = (off, zoom, bank, x, y)
            # else SEEN: second encounter -> bake below
        else:
            # FIRST encounter: mark SEEN only (one-offs must not flood the arena)
            self.index[slot] = dict(key=key, state='SEEN')
            r = ptc.TickCost.draw(self, off, x, y, zoom, col)
            self.stat['seen'] += 1
            self.depth -= 1
            return r

        if poskey is not None:
            pslot = self.hslot2(poskey)
            pe = self.index2[pslot]
            if pe is not None and pe['key'] == poskey:
                if pe['state'] == 'CELL':
                    self._blit_cell(pe, x, y)   # exact position -> exact bytes
                    self.stat['hit'] += 1
                    self.depth -= 1
                    return
                if pe['state'] == 'NEVER':
                    r = ptc.TickCost.draw(self, off, x, y, zoom, col)
                    self.stat['never_draw'] += 1
                    self.depth -= 1
                    return r
                # SEEN at this position -> bake below
            else:
                self.index2[pslot] = dict(key=poskey, state='SEEN')
                r = ptc.TickCost.draw(self, off, x, y, zoom, col)
                self.stat['seen'] += 1
                self.depth -= 1
                return r
            bakex, bakey = x, y                 # bake AT the position (clipped = OK)
        else:
            bakex = 160 | (x & 1)               # centre, same x parity as the draw
            bakey = 100

        # ---- bake ----
        self.kinds = set()
        if poskey is not None:
            scr, self.bb = self._bake_strip(off, bakex, bakey, zoom, col)
            SW, CH = LW, H
            baked = (scr, SW, CH, self.bb, bakex, bakey) if self.bb else None
        else:
            baked = self._bake_cell(off, zoom, col, x)

        if baked is None:
            if STAGE1:
                self._set_never(key, poskey)     # stage 1: no POSKEY fallback
                ptc.TickCost.draw(self, off, x, y, zoom, col)
                self.stat['edge'] += 1
                self.depth -= 1
                return
            if poskey is None:
                # uncacheable position-free (too tall/wide/empty) -> try fixed-
                # position cells (exact clipped content); scrolling monsters
                # just stay SEEN there, costing nothing extra
                self.index[slot] = dict(key=key, state='POSKEY')
                self.stat['edge'] += 1
                self.depth -= 1
                return self.draw(off, x, y, zoom, col)
            self._set_never(key, poskey)
            ptc.TickCost.draw(self, off, x, y, zoom, col)
            self.stat['special'] += 1
            self.depth -= 1
            return
        scr, SW, CH, self.bb, bakex, bakey = baked

        if self.kinds & {'dots', 'clipped'}:    # dots: device plot is position-
            self._set_never(key, poskey)        #   dependent (x >= 256 skip);
            ptc.TickCost.draw(self, off, x, y, zoom, col)   # clipped: a fill
            self.stat['special'] += 1           #   may have lost content
            self.depth -= 1                     #   invisibly (off-screen child)
            return

        bx0, by0, bx1, by1 = self.bb
        if poskey is None and (bx0 == 0 or by0 == 0 or bx1 == SW - 1 or by1 == CH - 1):
            # content still touches the (possibly stitched) canvas edge ->
            # the bake is clipped -> fall back to fixed-position cells
            self.index[slot] = dict(key=key, state='POSKEY')
            self.stat['edge'] += 1
            self.depth -= 1
            return self.draw(off, x, y, zoom, col)

        w = bx1 - bx0 + 1; h = by1 - by0 + 1
        size = w * h
        if size > CELL_MAX:
            self._set_never(key, poskey)
            ptc.TickCost.draw(self, off, x, y, zoom, col)
            self.stat['toobig'] += 1
            self.depth -= 1
            return
        cell = [scr[(by0 + r) * SW + bx0:(by0 + r) * SW + bx0 + w]
                for r in range(h)]
        classes = set()
        for row in cell:
            for v in row:
                if v == 0: continue
                classes.add('or' if v == 0x08 else
                            'solid' if v >= 0xF0 else
                            'p0+or' if v == 0x28 else 'p0')
        has_p0 = 'p0' in classes or 'p0+or' in classes
        has_or = 'or' in classes or 'p0+or' in classes
        has_solid = 'solid' in classes
        if STAGE1 and (has_p0 or has_or):
            self._set_never(key, poskey)         # stage 1: pure-solid cells only
            ptc.TickCost.draw(self, off, x, y, zoom, col)
            self.stat['special'] += 1
            self.depth -= 1
            return
        nclass = (1 if has_p0 else 0) + (1 if has_or else 0) + (1 if has_solid else 0)
        if not self.alloc(size * nclass):       # no room for THIS cell -> NEVER it
            self._set_never(key, poskey)        #   (others may still fit later)
            ptc.TickCost.draw(self, off, x, y, zoom, col)
            self.stat['nofit'] += 1
            self.depth -= 1
            return
        # the 6502 separates classes by extra mask/value re-renders; burn the
        # same render count in the model so the bake cost stays honest:
        #   pure: 0 extra | solid+or: +2 (value+mask) +1 if p0 | else +1/class
        extra = 0
        if len(classes) > 1 or has_p0:
            extra = (2 if (has_solid and has_or) else 1 if has_solid else 0) \
                + (1 if has_or else 0) + (1 if has_p0 else 0)
            nstrips = (2 if SW > LW else 1) * (2 if CH > H else 1)
            for _ in range(extra * nstrips):
                self._bake_strip(off, bakex, bakey, zoom, col)
        e = dict(key=poskey if poskey else key, state='CELL', cell=cell,
                 has_p0=has_p0, has_or=has_or, has_solid=has_solid,
                 w=w, h=h, bx0=bx0, by0=by0, bakex=bakex, bakey=bakey)
        if poskey:
            self.index2[self.hslot2(poskey)] = e
            self.index[self.hslot(key)] = dict(key=key, state='POSKEY')
        else:
            self.index[self.hslot(key)] = e
        self._blit_cell(e, x, y)                # first draw, clipped blit
        self.stat['bake'] += 1
        self.blit_cyc += C_BLIT_SETUP + (21 + 2 * w) * h / 8   # the arena rect copy
        self.depth -= 1

    def _bake_cell(self, off, zoom, col, x):
        """Probe + strip-compose a position-free cell on a virtual canvas of up
        to 2x2 screen-sized strips (319 x 398). Returns (canvas, CW, CH, bb,
        bakex, bakey) or None when uncacheable (taller than ~390 px / wider
        than 636 px / empty). Group headers lie about the bbox, so everything
        is discovered from rendered extents:
          probe at centre -> true content top/bottom (extra probes shifted by
          +-198 when an edge is clipped) -> vertical placement (content top at
          canvas row 2) -> horizontal overflow -> strip grid."""
        p = x & 1
        cx0 = 160 | p
        scr, bb = self._bake_strip(off, cx0, 100, zoom, col)
        if bb is None:
            return None
        # discover the true vertical extent in shape-local rows (local = screen - cy)
        t = bb[1] - 100 if bb[1] > 0 else None
        b = bb[3] - 100 if bb[3] < H - 1 else None
        if t is None:
            s2, bb2 = self._bake_strip(off, cx0, 298, zoom, col)
            if bb2 and bb2[1] > 0:
                t = bb2[1] - 298
        if b is None:
            s2, bb2 = self._bake_strip(off, cx0, -96, zoom, col)
            if bb2 and bb2[3] < H - 1:
                b = bb2[3] + 96
        if t is None or b is None:
            return None                          # > ~500 px tall
        vh = b - t + 1
        vstrip = vh > 196
        if vh > 392:
            return None
        hstrip = bb[0] == 0 or bb[2] == LW - 1   # horizontal clip is cy-independent
        if bb[0] > 0 and bb[2] < LW - 1 and bb[1] > 0 and bb[3] < H - 1:
            return (scr, LW, H, bb, cx0, 100)    # untouched probe IS the cell render
        if STAGE1:
            return None                          # stage 1: no strips / re-placement
        bakey = 2 - t                            # content top -> canvas row 2
        cxs = [318 + p, p] if hstrip else [cx0]
        cys = [bakey, bakey - 198] if vstrip else [bakey]
        CW = 319 if hstrip else LW
        CH = 398 if vstrip else H
        canvas = bytearray(CW * CH)
        bbout = None
        for vi, cy in enumerate(cys):
            for hi, cx in enumerate(cxs):
                s, sb = self._bake_strip(off, cx, cy, zoom, col)
                ox, oy = hi * 159, vi * 198
                for r in range(H):
                    dst = (oy + r) * CW + ox
                    seg = s[r * LW:(r + 1) * LW]
                    if hi or vi:                 # stitch sanity (translation-exact)
                        for j in range(LW):
                            old = canvas[dst + j]
                            if old and seg[j] != old:
                                return None      # never expected; bail safely
                    canvas[dst:dst + LW] = seg
                if sb:
                    sbb = [sb[0] + ox, sb[1] + oy, sb[2] + ox, sb[3] + oy]
                    if bbout is None:
                        bbout = sbb
                    else:
                        bbout = [min(bbout[0], sbb[0]), min(bbout[1], sbb[1]),
                                 max(bbout[2], sbb[2]), max(bbout[3], sbb[3])]
        if bbout is None:
            return None
        return (canvas, CW, CH, bbout,
                (318 + p) if hstrip else cx0, bakey)

    def _bake_strip(self, off, cx, cy, zoom, col):
        """One bake render at (cx,cy) on a blank scratch page -> (bytes, bb)."""
        pg = self.pages[self.cur1]
        for i in range(LW * H):
            self.scratch[i] = 0
        self.pages[self.cur1] = self.scratch
        self.baking = True; self.bb = None
        ptc.TickCost.draw(self, off, cx, cy, zoom, col)
        self.baking = False
        self.pages[self.cur1] = pg
        return bytes(self.scratch), self.bb

    def _set_never(self, key, poskey):
        if poskey:
            self.index2[self.hslot2(poskey)] = dict(key=poskey, state='NEVER')
        else:
            self.index[self.hslot(key)] = dict(key=key, state='NEVER')

    # ---- reuse ------------------------------------------------------------
    def _blit_cell(self, e, x, y):
        dx = (x - e['bakex']) >> 1              # parity-matched -> exact
        dy = y - e['bakey']
        self._stamp_cell(e['cell'], e['w'], e['h'], e['bx0'] + dx, e['by0'] + dy)
        # blits: p0 = 4-blit chain, solid = AND+OR pair (post-AND stencil cannot
        # write colour 0), or = 1 (full-res cells)
        nblit = ((4 if e['has_p0'] else 0) + (2 if e['has_solid'] else 0)
                 + (1 if e['has_or'] else 0))
        self.blit_cyc += nblit * (C_BLIT_SETUP + (21 + 2 * e['w']) * e['h'] / 8)

    def _stamp_cell(self, cell, w, h, dx0, dy0):
        # byte semantics == the p0 -> solid -> or blit order on the 6502
        page = self.pages[self.cur1]
        p0 = self.pages[0]
        for r in range(h):
            py = dy0 + r
            if py < 0 or py >= H:
                continue                         # vertical clip
            row = cell[r]
            base = py * LW
            for j in range(w):
                px = dx0 + j
                if px < 0 or px >= LW:
                    continue                     # horizontal clip
                v = row[j]
                if v == 0:
                    continue
                if v == 0x08:
                    page[base + px] |= 0x08      # or
                elif v >= 0xF0:
                    page[base + px] = v & 0x0F   # solid: AND($F0)+OR($0F) blit pair
                elif v == 0x20:
                    page[base + px] = p0[base + px]          # p0 (live)
                else:                            # $28
                    page[base + px] = p0[base + px] | 0x08   # p0 then or


def run_pair(part, frames, inp):
    ref = game_atari.GameAtari(part); ref.input = inp
    ref.run(frames)
    vm = CellCacheVM(part); vm.input = inp
    base_upd = vm.OPS[0x10]
    last = collections.Counter(); blit_last = [0.0]

    def upd(self):
        r = base_upd(self)
        d = self.c - last
        last.clear(); last.update(self.c)
        self.per_frame.append((dict(d), self.var[0xFF] & 0xFF,
                               self.blit_cyc - blit_last[0]))
        blit_last[0] = self.blit_cyc
        return r
    vm.OPS = list(vm.OPS); vm.OPS[0x10] = upd
    vm.run(frames)

    n = min(len(ref.frames), len(vm.frames))
    bad = 0
    for i in range(n):
        if ref.frames[i][0] != vm.frames[i][0]:
            bad += 1
            if bad <= 3:
                d = sum(1 for a, b in zip(ref.frames[i][0], vm.frames[i][0]) if a != b)
                print(f"    FRAME {i} DIFFERS: {d} bytes")
    return vm, n, bad


def main():
    if len(sys.argv) > 1:
        parts = [(int(sys.argv[1]), 'part')]
        frames = int(sys.argv[2]) if len(sys.argv) > 2 else 250
    else:
        parts = [(16002, 'water'), (16005, 'arene'), (16003, 'jail')]
        frames = 250

    ok = True
    for part, name in parts:
        vm, n, bad = run_pair(part, frames, INPUT)
        s = vm.stat
        draws = s['hit'] + s['bake'] + s['special'] + s['never_draw'] \
            + s['toobig'] + s['edge'] + s['nofit'] + s['seen']
        vbl = []; over = 0
        for d, hold, bcyc in vm.per_frame:
            v = (ptc.cost_cyc(collections.Counter(d), halfres=True) + bcyc) / FRAME
            vbl.append(v)
            if v > max(1, hold): over += 1
        vbl.sort()
        med = vbl[len(vbl) // 2] if vbl else 0
        p90 = vbl[int(len(vbl) * 0.9)] if vbl else 0
        eq = "BYTE-IDENTICAL" if bad == 0 else f"{bad}/{n} frames DIFFER"
        if bad: ok = False
        print(f"{name:6}: {eq} ({n} frames) | draws {draws}: "
              f"hit {100*s['hit']/max(1,draws):.0f}%  bake {s['bake']}  "
              f"mixed-never {s['special']+s['never_draw']}  seen {s['seen']}  "
              f"toobig {s['toobig']}  edge {s['edge']}  nofit {s['nofit']}")
        print(f"        cached tick: med {med:.2f} vbl  p90 {p90:.2f}  "
              f"overrun {100*over/max(1,len(vbl)):.0f}%   "
              f"(uncached: water 8.2/97%, arene 7.2/91%, jail 7.8/62%)")
    print()
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
