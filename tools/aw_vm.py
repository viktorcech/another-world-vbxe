#!/usr/bin/env python3
"""
aw_vm.py - minimal Another World VM that PLAYS the intro and dumps PNG frames.

It runs the intro bytecode (#0x15) with the cinematic polygons (#0x16) and the
palette (#0x14), faithfully enough to reproduce the visuals: the 4 video pages,
polygon drawing, page fill/copy/flip and palette changes. Each updateDisplay
writes one PNG to out/frames/. This is the PC reference for the Atari port.

Run:
    python aw_pack.py          # make out/14|15|16.bin
    python aw_palette.py       # make out/intro_pal.bin
    python aw_vm.py [maxframes]
Frames land in out/frames/fNNNN.png  -> open them yourself.
"""
import os, sys, struct
from PIL import Image, ImageDraw
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aw_render import Poly
import aw_text

OUT  = os.path.join(os.path.dirname(__file__), "..", "out")
FRAMES = os.path.join(OUT, "frames")
W, H = 320, 200
NO_REQ = 0xFFFF
INACTIVE = 0xFFFF


def load_palettes():
    p = open(os.path.join(OUT, "intro_pal.bin"), "rb").read()
    pals = []
    for k in range(32):
        flat = []
        for i in range(16):
            o = k*48 + i*3
            flat += [p[o] << 1, p[o+1] << 1, p[o+2] << 1]   # 7-bit -> 8-bit
        flat += [0] * (768 - len(flat))
        pals.append(flat)
    return pals


class VM:
    def __init__(self):
        self.code = open(os.path.join(OUT, "15.bin"), "rb").read()   # bytecode
        self.poly = Poly(open(os.path.join(OUT, "16.bin"), "rb").read())
        self.pals = load_palettes()
        self.var = [0]*256
        # known startup vars (timing / version markers)
        self.var[0x54] = 0x81
        self.var[0xBC] = 0x10
        self.var[0xC6] = 0x80
        self.var[0xF2] = 6000
        self.var[0xDC] = 33
        self.var[0xE4] = 20
        # 64 threads
        self.tpc   = [INACTIVE]*64
        self.treq  = [NO_REQ]*64
        self.tpause      = [0]*64
        self.tpause_req  = [0xFF]*64
        self.tpc[0] = 0                      # intro entry
        # video pages
        self.pages = [Image.new("P", (W, H), 0) for _ in range(4)]
        self.draws = [ImageDraw.Draw(p) for p in self.pages]
        self.cur1 = 2     # work page (draw target)
        self.cur2 = 2     # displayed page
        self.cur3 = 1     # back page
        self.nextpal = 0xFF
        self.curpal  = 0
        self.frame = 0
        self.running = True
        # per-thread exec state
        self.pc = 0
        self.stack = []
        self.goto_next = False

    # ---- bytecode fetch ----
    def b(self):
        v = self.code[self.pc]; self.pc += 1; return v
    def w(self):
        v = (self.code[self.pc] << 8) | self.code[self.pc+1]; self.pc += 2; return v
    def sw(self):
        v = self.w()
        return v - 0x10000 if v & 0x8000 else v

    def getpage(self, p):
        if p <= 3: return p
        if p == 0xFF: return self.cur3
        if p == 0xFE: return self.cur2
        return 0

    # ---- one thread runs until it yields ----
    def run_thread(self, t):
        self.pc = self.tpc[t]
        self.stack = []
        self.goto_next = False
        guard = 0
        while not self.goto_next:
            guard += 1
            if guard > 1_000_000:
                self.running = False; return
            op = self.b()
            if op & 0x80:
                self.draw_bg(op)
            elif op & 0x40:
                self.draw_sprite(op)
            else:
                self.OPS[op](self)
            if not self.running:
                return
        self.tpc[t] = self.pc

    # ---- polygon opcodes ----
    def draw_bg(self, op):
        off = (((op << 8) | self.b()) * 2) & 0xFFFF
        x = self.b(); y = self.b()
        h = y - 199
        if h > 0:
            y = 199; x += h
        try:
            self.poly.draw(self.draws[self.cur1], off, x, y, 64, 0xFF)
        except (IndexError, ValueError):
            pass

    def draw_sprite(self, op):
        off = (self.w() * 2) & 0xFFFF
        x = self.b()
        if not (op & 0x20):
            if not (op & 0x10): x = (x << 8) | self.b()
            else:               x = self.var[x]
        else:
            if op & 0x10:       x += 256
        y = self.b()
        if not (op & 8):
            if not (op & 4):    y = (y << 8) | self.b()
            else:               y = self.var[y]
        zoom = self.b()
        if not (op & 2):
            if not (op & 1):    self.pc -= 1; zoom = 64
            else:               zoom = self.var[zoom]
        else:
            if op & 1:          self.pc -= 1; zoom = 64
        try:
            self.poly.draw(self.draws[self.cur1], off, x, y, zoom, 0xFF)
        except (IndexError, ValueError):
            pass

    # ---- table opcodes 0x00-0x1A ----
    def op_movconst(self): v=self.b(); self.var[v]=self.sw()
    def op_mov(self):      d=self.b(); s=self.b(); self.var[d]=self.var[s]
    def op_add(self):      d=self.b(); s=self.b(); self.var[d]=(self.var[d]+self.var[s]) & 0xFFFF; self._sx(d)
    def op_addconst(self): v=self.b(); self.var[v]=(self.var[v]+self.sw()); self._sx(v)
    def op_call(self):     a=self.w(); self.stack.append(self.pc); self.pc=a
    def op_ret(self):
        if self.stack: self.pc=self.stack.pop()
        else: self.goto_next=True            # unbalanced ret -> end this thread slice
    def op_yield(self):    self.goto_next=True
    def op_jmp(self):      self.pc=self.w()
    def op_install(self):  t=self.b(); a=self.w(); self.treq[t]=a
    def op_djnz(self):
        v=self.b(); self.var[v]=(self.var[v]-1) & 0xFFFF; a=self.w()
        if self.var[v]!=0: self.pc=a
    def op_condjmp(self):
        sub=self.b(); v=self.b(); a=self.var[v]
        if sub & 0x80:   b=self.var[self.b()]
        elif sub & 0x40: b=self.sw()
        else:            b=self.b()
        self._sx(v); a=self.var[v]
        m=sub & 7
        c=(a==b) if m==0 else (a!=b) if m==1 else (a>b) if m==2 else \
          (a>=b) if m==3 else (a<b) if m==4 else (a<=b) if m==5 else False
        dst=self.w()
        if c: self.pc=dst
    def op_setpal(self):   self.nextpal=self.w() >> 8
    def op_resettask(self):
        first=self.b(); last=self.b(); typ=self.b()
        if last < first: return
        if typ==2:
            for i in range(first,last+1): self.treq[i]=0xFFFE
        else:
            for i in range(first,last+1): self.tpause_req[i]=typ
    def op_selpage(self):  self.cur1=self.getpage(self.b())
    def op_fillpage(self):
        pg=self.getpage(self.b()); col=self.b()
        self.draws[pg].rectangle([0,0,W-1,H-1], fill=col)
    def op_copypage(self):
        i=self.b(); j=self.b()
        src = self.getpage(i & 3) if (i & 0x80) else self.getpage(i)
        self.pages[self.getpage(j)].paste(self.pages[src], (0,0))
        self.draws[self.getpage(j)] = ImageDraw.Draw(self.pages[self.getpage(j)])
    def op_updatedisplay(self):
        page=self.b()
        if page != 0xFE:
            if page == 0xFF: self.cur2, self.cur3 = self.cur3, self.cur2
            else:            self.cur2 = self.getpage(page)
        if self.nextpal != 0xFF:
            self.curpal=self.nextpal; self.nextpal=0xFF
        self.snapshot()
        self.goto_next = True
    def op_remove(self):   self.tpc_cur_remove=True; self.pc=INACTIVE; self.goto_next=True; self._removed=True
    def op_drawstring(self):
        strId=self.w(); x=self.b(); y=self.b(); color=self.b()
        s=aw_text.STRINGS.get(strId)
        if s is None: return
        pg=self.pages[self.cur1]; cx=x
        for ch in s:
            if ch=='\n': y+=8; cx=x; continue
            oc=ord(ch)
            if 0x20<=oc<=0x7F:
                g=(oc-0x20)*8
                for j in range(8):
                    row=aw_text.FONT[g+j]; yy=y+j
                    if 0<=yy<H:
                        for i in range(8):
                            if row&(0x80>>i):
                                xx=cx*8+i
                                if 0<=xx<W: pg.putpixel((xx,yy),color)
            cx+=1
    def op_sub(self):      d=self.b(); s=self.b(); self.var[d]=(self.var[d]-self.var[s]); self._sx(d)
    def op_and(self):      v=self.b(); self.var[v]=(self.var[v] & self.w())
    def op_or(self):       v=self.b(); self.var[v]=(self.var[v] | self.w())
    def op_shl(self):      v=self.b(); self.var[v]=(self.var[v] << (self.w() & 15)) & 0xFFFF
    def op_shr(self):      v=self.b(); self.var[v]=((self.var[v] & 0xFFFF) >> (self.w() & 15))
    def op_sound(self):    self.w(); self.b(); self.b(); self.b()      # audio: skip
    def op_memlist(self):
        num=self.w()
        if num == 0: return
        if num >= 0x3E80:        # part switch -> intro is over
            self.running=False
    def op_music(self):    self.w(); self.w(); self.b()                # audio: skip

    def _sx(self, v):       # keep var signed 16-bit
        self.var[v] = ((self.var[v] + 0x8000) & 0xFFFF) - 0x8000

    def snapshot(self):
        img = self.pages[self.cur2].copy()
        img.putpalette(self.pals[self.curpal])
        img.convert("RGB").save(os.path.join(FRAMES, "f%04d.png" % self.frame))
        self.frame += 1

    # ---- frame loop ----
    def run(self, maxframes):
        os.makedirs(FRAMES, exist_ok=True)
        for old in os.listdir(FRAMES):
            os.remove(os.path.join(FRAMES, old))
        while self.running and self.frame < maxframes:
            # apply requested thread states
            for i in range(64):
                if self.tpause_req[i] != 0xFF:
                    self.tpause[i] = self.tpause_req[i]; self.tpause_req[i] = 0xFF
                if self.treq[i] != NO_REQ:
                    self.tpc[i] = 0 if self.treq[i] == 0xFFFE else self.treq[i]
                    if self.treq[i] == 0xFFFE: self.tpc[i] = INACTIVE
                    self.treq[i] = NO_REQ
            ran = False
            for i in range(64):
                if self.tpause[i]: continue
                if self.tpc[i] == INACTIVE: continue
                ran = True
                self._removed=False
                self.run_thread(i)
                if getattr(self,"_removed",False): self.tpc[i]=INACTIVE
                if not self.running: break
            if not ran:
                break


def main():
    maxf = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    vm = VM()
    vm.run(maxf)
    print(f"wrote {vm.frame} frames to {os.path.normpath(FRAMES)}/")


# opcode dispatch table (bound after class definition)
VM.OPS = [
    VM.op_movconst, VM.op_mov, VM.op_add, VM.op_addconst, VM.op_call, VM.op_ret,
    VM.op_yield, VM.op_jmp, VM.op_install, VM.op_djnz, VM.op_condjmp, VM.op_setpal,
    VM.op_resettask, VM.op_selpage, VM.op_fillpage, VM.op_copypage,
    VM.op_updatedisplay, VM.op_remove, VM.op_drawstring, VM.op_sub, VM.op_and,
    VM.op_or, VM.op_shl, VM.op_shr, VM.op_sound, VM.op_memlist, VM.op_music,
]

if __name__ == "__main__":
    main()
