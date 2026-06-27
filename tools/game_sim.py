#!/usr/bin/env python3
"""game_sim.py - Another World GAME simulator / oracle (Python).

aw_sim.py is the INTRO oracle: it hardcodes the intro resources, has no input,
and stops at the part switch. game_sim.py is the GAME oracle -- it loads any
PART, handles the two polygon banks (video1 = part shapes, video2 = shared
common shapes), the player input variables, and PART SWITCHING. It is the
reference the 6502 game VM is verified against (frame + variable state).

Faithful to rawgl (cyxx/rawgl) script.cpp / resource.cpp:
  - _memListParts: each part = {palette, bytecode, video1, video2}.
  - draw_sprite (op&0x40): when (op&2)&&(op&1) the shape is read from video2.
  - input vars: VAR_HERO_* (0xE5,0xFA..0xFE) set from a joystick mask each frame.

Run:
    python tools/game_sim.py 16002 60    # run part 16002 (water) for 60 frames
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aw_pack
import aw_sim
from aw_sim import PolyData, fill_poly_int, fill_poly, INACTIVE, NO_REQ, SIZE, palettes

# rawgl Resource::_memListParts  {palette, bytecode, video1, video2}
MEMLIST_PARTS = [
    (0x14, 0x15, 0x16, 0x00),   # 16000 protection
    (0x17, 0x18, 0x19, 0x00),   # 16001 intro
    (0x1A, 0x1B, 0x1C, 0x11),   # 16002 water  (gameplay start)
    (0x1D, 0x1E, 0x1F, 0x11),   # 16003 jail
    (0x20, 0x21, 0x22, 0x11),   # 16004 cite
    (0x23, 0x24, 0x25, 0x00),   # 16005 arene
    (0x26, 0x27, 0x28, 0x11),   # 16006 luxe
    (0x29, 0x2A, 0x2B, 0x11),   # 16007 final
    (0x7D, 0x7E, 0x7F, 0x00),   # 16008 password
]
FIRST_PART = 16000

# input variable indices (rawgl script.h)
VAR_RANDOM_SEED = 0x3C
VAR_HERO_POS_UP_DOWN = 0xE5
VAR_SCROLL_Y = 0xF9
VAR_HERO_ACTION = 0xFA
VAR_HERO_POS_JUMP_DOWN = 0xFB
VAR_HERO_POS_LEFT_RIGHT = 0xFC
VAR_HERO_POS_MASK = 0xFD
VAR_HERO_ACTION_POS_MASK = 0xFE
VAR_PAUSE_SLICES = 0xFF


class GameVM(aw_sim.VM):
    def __init__(self, part=16002, engine='int', seed=0):
        self.engine = engine
        self.mem = aw_pack.read_memlist()
        self.pals = None
        self.poly = None      # video1 (part shapes)
        self.poly2 = None     # video2 (shared shapes)
        self.use_video2 = False
        self.var = [0] * 256
        self.var[VAR_RANDOM_SEED] = seed
        # AW startup variable markers (same as aw_sim / the original)
        # rawgl BYPASS_PROTECTION: the game bytecode expects these to be set by the
        # copy-protection screen on exit. VAR 0xF2 == 4000 is required, else the water
        # part's thread 0 branches to a dead path (kills all threads). 6000 was wrong.
        for k, v in ((0x54, 0x81), (0xBC, 0x10), (0xC6, 0x80),
                     (0xF2, 4000), (0xDC, 33), (0xE4, 20)):
            self.var[k] = v
        self.tpc = [INACTIVE] * 64
        self.treq = [NO_REQ] * 64
        self.tpause = [0] * 64
        self.tpause_req = [0xFF] * 64
        self.pages = [bytearray(SIZE) for _ in range(4)]
        self.cur1 = 2; self.cur2 = 2; self.cur3 = 1
        self.nextpal = 0xFF; self.curpal = 0
        self.frames = []
        self.maxframes = 1 << 30        # default cap; run() lowers it, step() uses it as-is
        self.running = True
        self.pc = 0; self.stack = []; self.goto = False
        self.removed = False
        self.draws = 0
        self.drawlist = []
        self.events = []
        self.next_part = None
        self.input = 0          # joystick mask (DIR_RIGHT=1,LEFT=2,DOWN=4,UP/JUMP=8,ACTION=0x80)
        self.load_part(part)

    # ---- resources / parts ----
    def load_part(self, part):
        pa, co, v1, v2 = MEMLIST_PARTS[part - FIRST_PART]
        pal = aw_pack.load_resource(self.mem[pa])[0]
        self.code = aw_pack.load_resource(self.mem[co])[0]
        v1d = aw_pack.load_resource(self.mem[v1])[0]
        fill = fill_poly_int if self.engine == 'int' else fill_poly
        self.pals = palettes(pal)
        self.poly = PolyData(v1d, fill)
        if v2:
            self.poly2 = PolyData(aw_pack.load_resource(self.mem[v2])[0], fill)
        else:
            self.poly2 = None
        # reset threads, start thread 0 at pc 0
        self.tpc = [INACTIVE] * 64
        self.treq = [NO_REQ] * 64
        self.tpause = [0] * 64
        self.tpause_req = [0xFF] * 64
        self.tpc[0] = 0
        self.cur_part = part

    # ---- input : set the hero variables from the joystick mask (rawgl updateInput) ----
    def update_input(self):
        inp = self.input
        lr = ud = jd = 0; m = 0
        if inp & 1: lr = 1;  m |= 1   # right
        if inp & 2: lr = -1; m |= 2   # left
        if inp & 4: ud = 1; jd = 1; m |= 4   # down
        if inp & 8: ud = -1; jd = -1; m |= 8 # up / jump
        self.var[VAR_HERO_POS_UP_DOWN] = ud & 0xFFFF
        self.var[VAR_HERO_POS_JUMP_DOWN] = jd & 0xFFFF
        self.var[VAR_HERO_POS_LEFT_RIGHT] = lr & 0xFFFF
        self.var[VAR_HERO_POS_MASK] = m
        action = 1 if (inp & 0x80) else 0
        self.var[VAR_HERO_ACTION] = action
        self.var[VAR_HERO_ACTION_POS_MASK] = m | (action << 7)

    # ---- draw_sprite override : video2 selection (rawgl op 0x40) ----
    def draw_sprite(self, op):
        off = (self.w() * 2) & 0xFFFF
        x = self.b()
        self.use_video2 = False
        if not (op & 0x20):
            if not (op & 0x10): x = (x << 8) | self.b()
            else: x = self.var[x]
        else:
            if op & 0x10: x += 256
        y = self.b()
        if not (op & 8):
            if not (op & 4): y = (y << 8) | self.b()
            else: y = self.var[y]
        zoom = 64
        if not (op & 2):
            if op & 1: zoom = self.var[self.b()]
        else:
            if op & 1: self.use_video2 = True       # <-- video2 (shared shapes)
            else: zoom = self.b()
        x = ((x & 0xFFFF) ^ 0x8000) - 0x8000        # int16 sign-extend
        y = ((y & 0xFFFF) ^ 0x8000) - 0x8000
        poly = self.poly2 if (self.use_video2 and self.poly2) else self.poly
        poly.page0 = self.pages[0]
        poly.draw(self.pages[self.cur1], off, x, y, zoom, 0xFF)
        # record the bank (1=video1 part shapes, 2=video2 shared shapes) so the GUI
        # can describe/expand the right polygon buffer. bg draws are always video1.
        bank = 2 if (self.use_video2 and self.poly2) else 1
        self.draws += 1; self.drawlist.append(('spr', off, x, y, zoom, bank))

    # ---- op_memlist override : part switch ----
    def load_bitmap(self, me):
        """Decode a 32000-byte AW background bitmap (4 planes x 8000 B, LSB-first
        bit order) into page 0 -- the background buffer scenes copyPage from. luxe
        & other scenes load these via op_memlist; without it the room is missing
        (black). 320-wide pages get the full image; 160-LR pages keep even columns
        (the same downsample the LR polygon path uses)."""
        data, _ok = aw_pack.load_resource(me)
        if len(data) < 32000:
            return
        pg = self.pages[0]
        lw = len(pg) // 200                     # 320 (PC) or 160 (ATARI LR)
        for y in range(200):
            base = y * lw
            for xb in range(40):
                o = y * 40 + xb
                b0, b1, b2, b3 = data[o], data[o + 8000], data[o + 16000], data[o + 24000]
                for bit in range(8):
                    m = 1 << bit                # LSB-first
                    c = (1 if b0 & m else 0) | (2 if b1 & m else 0) \
                        | (4 if b2 & m else 0) | (8 if b3 & m else 0)
                    x = xb * 8 + (7 - bit)      # pixel x in 320-space
                    if lw == 160:
                        if x & 1:
                            continue            # LR: keep even columns
                        pg[base + (x >> 1)] = c
                    else:
                        pg[base + x] = c

    def op_memlist(self):
        num = self.w()
        if num == 0:
            return
        if num >= 0x3E80:           # >= 16000 : switch to that part
            self.next_part = num - 0x3E80 + FIRST_PART
            self.goto = True        # end this thread slice; run() applies the switch
            return
        if num < len(self.mem):     # < 16000 : a global resource. 32 KB type-2 = a
            me = self.mem[num]      #   background BITMAP -> decode it onto page 0.
            if getattr(me, 'type', None) == 2 and getattr(me, 'size', 0) == 32000:
                self.load_bitmap(me)

    # ---- op_copypage override : honour VAR_SCROLL_Y (the base aw_sim ignored it) ----
    def op_copypage(self):
        """copypage with vertical scroll. src 0x80..0xBF = a copy of page (src&3)
        shifted vertically by VAR_SCROLL_Y rows (rawgl/another.js copy_page). The
        elevator shaft TILES its rocky walls down by repeatedly copying page 3 while
        bumping VAR_SCROLL_Y (+=32) -- ignoring the scroll collapsed every tile onto
        the top, so the floor and lower walls were missing."""
        i = self.b(); j = self.b()
        dst = self.page(j)
        dstb = self.pages[dst]
        if i >= 0xFE:                                   # cur2/cur3 back buffers, no scroll
            dstb[:] = self.pages[self.page(i)][:]
            return
        vscroll = self.var[VAR_SCROLL_Y] if (i & 0x80) else 0
        if vscroll >= 0x8000:                           # signed 16-bit
            vscroll -= 0x10000
        src = self.page(i & 3)
        if dst == src:
            return
        srcb = self.pages[src]
        if vscroll == 0:
            dstb[:] = srcb[:]
            return
        ps = len(dstb)
        wp = ps // 200                                  # page width (320 PC / 160 LR)
        h = vscroll * wp                                # pixel offset of the shift
        if h <= -ps or h >= ps:
            return                                      # scrolled fully off-screen
        if vscroll < 0:                                 # content moves up
            a = -h
            dstb[0:ps-a] = srcb[a:ps]
        else:                                           # content moves down
            dstb[h:ps] = srcb[0:ps-h]

    # ---- one scheduler pass (one display 'tick') : the live-play unit ----
    def step(self):
        """Advance the VM by ONE scheduler pass (apply part switch + input + thread
        requests, then run every active thread until it yields). Returns the number
        of display frames produced this pass (0 means the VM halted). The GUI calls
        this once per tick so keyboard input drives the hero in real time."""
        if not self.running:
            return 0
        start = len(self.frames)
        if self.next_part is not None:
            self.load_part(self.next_part); self.next_part = None
        self.update_input()
        for i in range(64):
            if self.tpause_req[i] != 0xFF:
                self.tpause[i] = self.tpause_req[i]; self.tpause_req[i] = 0xFF
            if self.treq[i] != NO_REQ:
                self.tpc[i] = INACTIVE if self.treq[i] == 0xFFFE else self.treq[i]
                self.treq[i] = NO_REQ
        ran = False
        for i in range(64):
            if self.tpause[i] or self.tpc[i] == INACTIVE:
                continue
            ran = True
            self.run_thread(i)
            if self.removed:
                self.tpc[i] = INACTIVE
            if self.next_part is not None:
                break               # part switch requested -> reload before next pass
            if not self.running:
                break
        if not ran and self.next_part is None:
            self.running = False    # no active threads left -> VM is dead
        return len(self.frames) - start

    # ---- frame loop with input + part switching ----
    def run(self, maxframes=400):
        self.maxframes = maxframes
        while self.running and len(self.frames) < maxframes:
            self.step()
        return self.frames


# GameVM needs its own OPS table so the overridden op_memlist is dispatched.
GameVM.OPS = list(aw_sim.VM.OPS)
GameVM.OPS[0x19] = GameVM.op_memlist
GameVM.OPS[0x0F] = GameVM.op_copypage    # vertical-scroll copy (elevator shaft tiling)


def main():
    part = int(sys.argv[1]) if len(sys.argv) > 1 else 16002
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    vm = GameVM(part)
    frames = vm.run(n)
    print(f"part {part}: rendered {len(frames)} frames (no input)")
    for idx in range(0, len(frames), max(1, len(frames) // 10)):
        page, pal, hold, draws, dl = frames[idx]
        nz = sum(1 for c in page if c) * 100 // SIZE
        print(f"  f{idx:4} pal={pal:2} hold={hold:3} draws={draws:3} non-empty={nz:3}%")


if __name__ == "__main__":
    main()
