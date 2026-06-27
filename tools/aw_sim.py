#!/usr/bin/env python3
"""
aw_sim.py - pure-Python Another World intro simulator (no PIL, stdlib only).

Decodes + runs the intro bytecode exactly like the original VM and rasterises
the flat-shaded polygons the same way the Atari/VBXE port will (horizontal
spans into a chunky 1-byte-per-pixel page). render_intro() returns a list of
frames; the GUI (gui.py) drives it. This is the PC reference / golden oracle.

Reused by gui.py; can also self-test from the command line:
    python aw_sim.py            # run VM, print per-frame content stats
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aw_pack
import aw_text

W, H = 320, 200
SIZE = W * H
NO_REQ, INACTIVE = 0xFFFF, 0xFFFF


# ---------------------------------------------------------------------------
# resource loading (palette + bytecode + cinematic polygons)
# ---------------------------------------------------------------------------
def load():
    mem = aw_pack.read_memlist()
    pal  = aw_pack.load_resource(mem[aw_pack.INTRO_PALETTE])[0]
    code = aw_pack.load_resource(mem[aw_pack.INTRO_BYTECODE])[0]
    poly = aw_pack.load_resource(mem[aw_pack.INTRO_POLY])[0]
    return pal, code, poly


def palettes(pal_res):
    """32 palettes x 16 colours, 4-bit -> 8-bit. The resource is VGA (first 1024
    bytes, clean 0x0RGB) then EGA (second 1024, garbage high nibbles + darker);
    the game uses the VGA half at offset 0 (rawgl: _segVideoPal+palNum*32)."""
    base = 0
    out = []
    for k in range(32):
        cols = []
        for i in range(16):
            o = base + k*32 + i*2
            v = (pal_res[o] << 8) | pal_res[o+1]
            r = ((v >> 8) & 0xF); g = ((v >> 4) & 0xF); b = (v & 0xF)
            cols.append((((r << 4) | r) >> 1 << 1,        # 4b->8b
                         ((g << 4) | g) >> 1 << 1,
                         ((b << 4) | b) >> 1 << 1))
        out.append(cols)
    return out


def frame_to_rgb(page, pal):
    """Indexed page (bytearray) -> packed RGB bytes through palette `pal`."""
    lut = bytearray(256 * 3)
    for i, (r, g, b) in enumerate(pal):
        lut[i*3] = r; lut[i*3+1] = g; lut[i*3+2] = b
    out = bytearray(SIZE * 3)
    for p in range(SIZE):
        c = page[p] * 3
        o = p * 3
        out[o] = lut[c]; out[o+1] = lut[c+1]; out[o+2] = lut[c+2]
    return bytes(out)


# ---------------------------------------------------------------------------
# polygon rasteriser (even-odd scanline fill = same filled region as the AW
# quad-strip fill; horizontal spans, exactly what the 6502+blitter produces)
# ---------------------------------------------------------------------------
def _span(page, page0, row, xa, xb, color):
    """Fill one horizontal span honouring AW's special colours:
       <0x10 = solid palette index, 0x10 = transparent (brighten, |8),
       >0x10 = copy from page 0 (show the background through the polygon)."""
    if color < 0x10:
        page[row+xa: row+xb+1] = bytes([color]) * (xb - xa + 1)
    elif color == 0x10:
        for p in range(row+xa, row+xb+1):
            page[p] |= 0x08
    else:
        page[row+xa: row+xb+1] = page0[row+xa: row+xb+1]


def fill_poly(page, page0, pts, color):
    n = len(pts)
    if n == 0:
        return
    if n < 3:                                   # degenerate: dot / line
        for (x, y) in pts:
            if 0 <= x < W and 0 <= y < H:
                _span(page, page0, y*W, x, x, color)
        return
    ys = [p[1] for p in pts]
    ymin = max(0, min(ys)); ymax = min(H-1, max(ys))
    for y in range(ymin, ymax+1):
        xs = []
        for k in range(n):
            x0, y0 = pts[k]; x1, y1 = pts[(k+1) % n]
            if y0 == y1:
                continue
            if min(y0, y1) <= y < max(y0, y1):
                xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
        xs.sort()
        row = y * W
        for m in range(0, len(xs) - 1, 2):
            xa = int(xs[m] + 0.5); xb = int(xs[m+1] + 0.5)
            if xb < 0 or xa > W-1:
                continue
            xa = 0 if xa < 0 else xa
            xb = W-1 if xb > W-1 else xb
            _span(page, page0, row, xa, xb, color)


# ---------------------------------------------------------------------------
# 6502-FAITHFUL polygon fill (integer 16.16 fixed-point, exactly the math the
# assembler will run -- NOT float). Quad-strip fill: walk the left & right
# edges down by an EXACT fixed-point slope step = (dx << 16) / dy, computed
# once per edge with a 32/16 unsigned divide (examples/math/div32.asm) and the
# sign applied separately (truncation toward zero, like an abs-then-negate
# divide). This is more precise than AW's reciprocal-table approximation
# (0x4000/dy * 4) and is what the 6502 port will run.
# ---------------------------------------------------------------------------
def _slope(dx, dy):                              # exact 16.16 step, 32-bit two's comp
    m = (abs(dx) << 16) // dy
    return (-m if dx < 0 else m) & 0xFFFFFFFF


def _i16(cpt):                                   # signed 16-bit high word of 16.16
    s = (cpt >> 16) & 0xFFFF
    return s - 0x10000 if s & 0x8000 else s


def fill_poly_int(page, page0, pts, color):
    n = len(pts)
    if n < 3:
        for (x, y) in pts:
            if 0 <= x < W and 0 <= y < H:
                _span(page, page0, y*W, x, x, color)
        return
    i = 0; j = n - 1
    hy = pts[0][1]                               # top scanline
    cr = (pts[j][0] & 0xFFFF) << 16              # right edge x, 16.16
    cl = (pts[i][0] & 0xFFFF) << 16              # left  edge x, 16.16
    i += 1; j -= 1
    numv = n
    while True:
        numv -= 2
        if numv == 0:
            return
        h = pts[i][1] - pts[i-1][1]              # segment height (both edges)
        dvr = (pts[j][0] - pts[j+1][0])
        dvl = (pts[i][0] - pts[i-1][0])
        hh = h if h > 0 else 1
        step_r = _slope(dvr, hh)
        step_l = _slope(dvl, hh)
        i += 1; j -= 1
        cr = (cr & 0xFFFF0000) | 0x7FFF          # AW rounding bias
        cl = (cl & 0xFFFF0000) | 0x8000
        if h == 0:
            cr = (cr + step_r) & 0xFFFFFFFF
            cl = (cl + step_l) & 0xFFFFFFFF
            continue
        for _ in range(h):
            if 0 <= hy < H:
                xr = _i16(cr); xl = _i16(cl)
                a, b = (xl, xr) if xl <= xr else (xr, xl)
                if a <= W-1 and b >= 0:
                    a = 0 if a < 0 else a
                    b = W-1 if b > W-1 else b
                    _span(page, page0, hy*W, a, b, color)
            cr = (cr + step_r) & 0xFFFFFFFF
            cl = (cl + step_l) & 0xFFFFFFFF
            hy += 1
            if hy > H-1:
                return


class PolyData:
    def __init__(self, data, fill=fill_poly):
        self.d = data
        self.fill = fill        # fill_poly (float ref) or fill_poly_int (6502)
        self.page0 = None       # background page (set by the VM before a draw)

    def draw(self, page, off, x, y, zoom, color):
        d = self.d
        i = d[off]; off += 1
        if i >= 0xC0:
            col = (i & 0x3F) if (color & 0x80) else color
            self._fill(page, off, col, zoom, x, y)
        else:
            if (i & 0x3F) == 2:
                self._hier(page, off, zoom, x, y, color)

    def _fill(self, page, off, color, zoom, ptx, pty):
        d = self.d
        bbw = d[off] * zoom // 64; off += 1
        bbh = d[off] * zoom // 64; off += 1
        n = d[off]; off += 1
        x0 = ptx - bbw // 2
        y0 = pty - bbh // 2
        pts = []
        for _ in range(n):
            px = x0 + d[off] * zoom // 64; off += 1
            py = y0 + d[off] * zoom // 64; off += 1
            pts.append((px, py))
        self.fill(page, self.page0, pts, color)

    def _hier(self, page, off, zoom, ptx, pty, color):
        d = self.d
        bx = ptx - d[off] * zoom // 64; off += 1
        by = pty - d[off] * zoom // 64; off += 1
        childs = d[off]; off += 1
        for _ in range(childs + 1):
            word = (d[off] << 8) | d[off+1]; off += 2
            cx = bx + d[off] * zoom // 64; off += 1
            cy = by + d[off] * zoom // 64; off += 1
            ccol = 0xFF
            if word & 0x8000:
                ccol = d[off] & 0x7F; off += 2
            self.draw(page, (word & 0x7FFF) * 2, cx, cy, zoom, ccol)


# ---------------------------------------------------------------------------
# the VM
# ---------------------------------------------------------------------------
class VM:
    def __init__(self, engine='float'):
        pal, self.code, polybin = load()
        self.pals = palettes(pal)
        self.poly = PolyData(polybin,
                             fill_poly_int if engine == 'int' else fill_poly)
        self.var = [0]*256
        for k, v in ((0x54,0x81),(0xBC,0x10),(0xC6,0x80),
                     (0xF2,6000),(0xDC,33),(0xE4,20)):
            self.var[k] = v
        self.tpc  = [INACTIVE]*64
        self.treq = [NO_REQ]*64
        self.tpause = [0]*64
        self.tpause_req = [0xFF]*64
        self.tpc[0] = 0
        self.pages = [bytearray(SIZE) for _ in range(4)]
        self.cur1 = 2; self.cur2 = 2; self.cur3 = 1
        self.nextpal = 0xFF; self.curpal = 0
        self.frames = []
        self.running = True
        self.pc = 0; self.stack = []; self.goto = False
        self.removed = False
        self.draws = 0
        self.drawlist = []        # per-frame record of polygon draws
        self.events = []          # flat render stream for the 6502 playlist

    # fetch
    def b(self):  v=self.code[self.pc]; self.pc+=1; return v
    def w(self):  v=(self.code[self.pc]<<8)|self.code[self.pc+1]; self.pc+=2; return v
    def sw(self): v=self.w(); return v-0x10000 if v&0x8000 else v
    def page(self,p): return p if p<=3 else (self.cur3 if p==0xFF else self.cur2 if p==0xFE else 0)
    def _sx(self,v): self.var[v]=((self.var[v]+0x8000)&0xFFFF)-0x8000

    def run_thread(self, t):
        self.pc=self.tpc[t]; self.stack=[]; self.goto=False; self.removed=False
        g=0
        while not self.goto:
            g+=1
            if g>2_000_000: self.running=False; return
            op=self.b()
            if op & 0x80:    self.draw_bg(op)
            elif op & 0x40:  self.draw_sprite(op)
            else:            self.OPS[op](self)
            if not self.running: return
        if not self.removed:
            self.tpc[t]=self.pc

    def draw_bg(self, op):
        off=(((op<<8)|self.b())*2)&0xFFFF
        x=self.b(); y=self.b()
        h=y-199
        if h>0: y=199; x+=h
        self.poly.page0=self.pages[0]
        self.poly.draw(self.pages[self.cur1], off, x, y, 64, 0xFF)
        self.draws+=1; self.drawlist.append(('bg', off, x, y, 64))
        self.events.append(('poly', off, x, y, 64))

    def draw_sprite(self, op):
        off=(self.w()*2)&0xFFFF
        x=self.b()
        if not(op&0x20):
            if not(op&0x10): x=(x<<8)|self.b()
            else: x=self.var[x]
        else:
            if op&0x10: x+=256
        y=self.b()
        if not(op&8):
            if not(op&4): y=(y<<8)|self.b()
            else: y=self.var[y]
        zoom=self.b()
        if not(op&2):
            if not(op&1): self.pc-=1; zoom=64
            else: zoom=self.var[zoom]
        else:
            if op&1: self.pc-=1; zoom=64
        # AW coordinates are int16_t: a word like 0xFFFE is x=-2 (sprite just off
        # the left), NOT 65534. Sign-extend here so the render, the recorded
        # event, and the flattened playlist all agree (else off-left sprites and
        # the y=-9 elevator-descent poly 50002 diverge from the replay).
        x = ((x & 0xFFFF) ^ 0x8000) - 0x8000
        y = ((y & 0xFFFF) ^ 0x8000) - 0x8000
        self.poly.page0=self.pages[0]
        self.poly.draw(self.pages[self.cur1], off, x, y, zoom, 0xFF)
        self.draws+=1; self.drawlist.append(('spr', off, x, y, zoom))
        self.events.append(('poly', off, x, y, zoom))

    # opcode table
    def op_movconst(self): v=self.b(); self.var[v]=self.sw()
    def op_mov(self): d=self.b(); s=self.b(); self.var[d]=self.var[s]
    def op_add(self): d=self.b(); s=self.b(); self.var[d]=self.var[d]+self.var[s]; self._sx(d)
    def op_addconst(self): v=self.b(); self.var[v]=self.var[v]+self.sw(); self._sx(v)
    def op_call(self): a=self.w(); self.stack.append(self.pc); self.pc=a
    def op_ret(self): self.pc=self.stack.pop()
    def op_yield(self): self.goto=True
    def op_jmp(self): self.pc=self.w()
    def op_install(self): t=self.b(); a=self.w(); self.treq[t]=a
    def op_djnz(self):
        v=self.b(); self.var[v]=(self.var[v]-1)&0xFFFF; a=self.w()
        if self.var[v]!=0: self.pc=a
    def op_condjmp(self):
        sub=self.b(); v=self.b(); self._sx(v); a=self.var[v]
        if sub&0x80: b=self.var[self.b()]
        elif sub&0x40: b=self.sw()
        else: b=self.b()
        m=sub&7
        c=(a==b) if m==0 else (a!=b) if m==1 else (a>b) if m==2 else \
          (a>=b) if m==3 else (a<b) if m==4 else (a<=b) if m==5 else False
        dst=self.w()
        if c: self.pc=dst
    def op_setpal(self): self.nextpal=self.w()>>8; self.events.append(('pal', self.nextpal))
    def op_resettask(self):
        first=self.b(); last=self.b(); typ=self.b()
        if last<first: return
        if typ==2:
            for i in range(first,last+1): self.treq[i]=0xFFFE
        else:
            for i in range(first,last+1): self.tpause_req[i]=typ
    def op_selpage(self): self.cur1=self.page(self.b()); self.events.append(('sel', self.cur1))
    def op_fillpage(self):
        pg=self.page(self.b()); col=self.b()
        self.pages[pg][:] = bytes([col])*SIZE
        self.events.append(('fill', pg, col))
    def op_copypage(self):
        i=self.b(); j=self.b()
        # AW order: 0xFE/0xFF = the back buffers (cur2/cur3) and MUST be tested
        # before the 0x80 bit. The old code let 0xFF (which has 0x80 set) fall
        # into the i&3 branch -> copied physical page 3 (black) instead of cur3,
        # turning the elevator-descent into a hard cut to black. (0x80..0xBF is a
        # scrolled copy of page i&3; we still ignore the VAR_SCROLL_Y offset.)
        if i >= 0xFE:
            src=self.page(i)
        elif i & 0x80:
            src=self.page(i & 3)
        else:
            src=self.page(i)
        dst=self.page(j)
        self.pages[dst][:] = self.pages[src][:]
        self.events.append(('copy', src, dst))
    def op_updatedisplay(self):
        pg=self.b()
        if pg!=0xFE:
            if pg==0xFF: self.cur2,self.cur3=self.cur3,self.cur2
            else: self.cur2=self.page(pg)
        if self.nextpal!=0xFF: self.curpal=self.nextpal; self.nextpal=0xFF
        self.frames.append((bytes(self.pages[self.cur2]), self.curpal,
                            self.var[0xFF], self.draws, tuple(self.drawlist)))
        self.events.append(('blit', self.cur2, self.var[0xFF] & 0xFF))
        self.draws=0; self.drawlist=[]
        # NB: blitFramebuffer does NOT end the thread slice -- only op_yield
        # (0x06) does. (A routine may blit several pages then ret.)
        if len(self.frames) >= self.maxframes:
            self.running=False; self.goto=True
    def op_remove(self): self.pc=INACTIVE; self.goto=True; self.removed=True
    def op_drawstring(self):
        strId=self.w(); x=self.b(); y=self.b(); color=self.b()
        aw_text.draw_string(self.pages[self.cur1], W, strId, x, y, color)
        self.draws+=1; self.drawlist.append(('txt', strId, x, y, color))
        self.events.append(('text', strId, x, y, color))
    def op_sub(self): d=self.b(); s=self.b(); self.var[d]=self.var[d]-self.var[s]; self._sx(d)
    def op_and(self): v=self.b(); self.var[v]=self.var[v]&self.w()
    def op_or(self): v=self.b(); self.var[v]=self.var[v]|self.w()
    def op_shl(self): v=self.b(); self.var[v]=(self.var[v]<<(self.w()&15))&0xFFFF
    def op_shr(self): v=self.b(); self.var[v]=(self.var[v]&0xFFFF)>>(self.w()&15)
    def op_sound(self):
        res=self.w(); freq=self.b(); vol=self.b(); ch=self.b()
        self.events.append(('snd', res, freq, vol, ch))
    def op_memlist(self):
        num=self.w()
        if num>=0x3E80: self.running=False     # part switch -> intro done
    def op_music(self): self.w(); self.w(); self.b()

    def run(self, maxframes=400):
        self.maxframes = maxframes
        while self.running and len(self.frames)<maxframes:
            for i in range(64):
                if self.tpause_req[i]!=0xFF:
                    self.tpause[i]=self.tpause_req[i]; self.tpause_req[i]=0xFF
                if self.treq[i]!=NO_REQ:
                    self.tpc[i]=INACTIVE if self.treq[i]==0xFFFE else self.treq[i]
                    self.treq[i]=NO_REQ
            ran=False
            for i in range(64):
                if self.tpause[i] or self.tpc[i]==INACTIVE: continue
                ran=True
                self.run_thread(i)
                if self.removed: self.tpc[i]=INACTIVE
                if not self.running: break
            if not ran: break
        return self.frames


VM.OPS = [VM.op_movconst,VM.op_mov,VM.op_add,VM.op_addconst,VM.op_call,VM.op_ret,
          VM.op_yield,VM.op_jmp,VM.op_install,VM.op_djnz,VM.op_condjmp,VM.op_setpal,
          VM.op_resettask,VM.op_selpage,VM.op_fillpage,VM.op_copypage,
          VM.op_updatedisplay,VM.op_remove,VM.op_drawstring,VM.op_sub,VM.op_and,
          VM.op_or,VM.op_shl,VM.op_shr,VM.op_sound,VM.op_memlist,VM.op_music]


def render_intro(maxframes=400, engine='float'):
    """engine='float' = smooth PC reference; engine='int' = the exact integer
    16.16 fixed-point raster the 6502 will run (use this for the ATARI panel)."""
    vm = VM(engine)
    frames = vm.run(maxframes)
    return frames, vm.pals


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    frames, pals = render_intro(n)
    print(f"frames: {len(frames)}")
    for idx, (page, pal, hold, draws, dl) in enumerate(frames):
        nz = sum(1 for c in page if c)            # non-background pixels
        if idx < 30 or idx % 10 == 0:
            print(f"  f{idx:3} pal={pal:2} hold={hold:3} polys={draws:3} "
                  f"non-empty={nz*100//SIZE:3}%")
