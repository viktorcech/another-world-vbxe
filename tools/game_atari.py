#!/usr/bin/env python3
"""
game_atari.py - faithful ATARI-LOW (VBXE LR 160) render of the GAME.

game_sim.py renders the game VM at the ideal 320 width (the PC oracle). This is the
ATARI panel's engine: it runs the SAME game VM (threads, opcodes, parts, input,
video1/video2) but rasterises through the EXACT 6502 LR pipeline that sim_atari.py
uses for the intro -- 160-wide pages, the 320-space 16.16 edge walk, the reciprocal-
LUT 8x16 slope math, emit_span's x>>1 LR mapping and fill_span's 3 colour modes.

So the ATARI panel is real Atari logic (thin features dropping on odd LR columns,
the 6502 slope rounding), NOT a keep-even-columns downsample of the 320 render --
exactly the distinction gui.py draws for the intro (sim_atari, not lr_sim).

This is also the GAME's Atari-LOW oracle: when the 6502 game VM exists, its page
must match GameAtari frame-for-frame, the same way sim_atari pins the intro port.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_sim
import sim_atari
from sim_atari import s16

LW, H = 160, 200            # the VBXE LR page is 160 bytes wide (1 byte = 2 hw px)
LSIZE = LW * H


class GameAtari(game_sim.GameVM):
    """The game VM rendered through sim_atari's faithful 160-LR rasteriser."""

    def __init__(self, part=16002, seed=0):
        super().__init__(part, 'int', seed)
        # swap the 320 pages the base VM allocated for the real 160-wide LR surface
        self.pages = [bytearray(LSIZE) for _ in range(4)]
        self._pd = None        # active poly byte buffer for the current draw (a bank)

    # the raster routines below are bound straight from sim_atari.Sim so the LR
    # pipeline is byte-identical to the proven intro Atari oracle. They reference
    # self.cur (draw page), self.by (poly fetch) and self.mul -- adapted here.
    @property
    def cur(self):
        return self.cur1

    def by(self, off):
        return self._pd[off & 0xFFFF]

    mul = sim_atari.Sim.mul
    calc_step = staticmethod(sim_atari.Sim.calc_step)   # keep it static (sig: dv, hh)
    draw = sim_atari.Sim.draw
    fill = sim_atari.Sim.fill
    hier = sim_atari.Sim.hier
    fill_poly_int = sim_atari.Sim.fill_poly_int
    span = sim_atari.Sim.span
    fill_span = sim_atari.Sim.fill_span
    draw_text = sim_atari.Sim.draw_text   # intro's LR glyph blitter (emit_span x>>1)

    # ---- draws: identical operand decode to game_sim, LR render instead of 320 ----
    def draw_bg(self, op):
        off = (((op << 8) | self.b()) * 2) & 0xFFFF
        x = self.b(); y = self.b()
        h = y - 199
        if h > 0:
            y = 199; x += h
        self._pd = self.poly.d
        self.draw(off, s16(x), s16(y), 64, 0xFF)
        self.draws += 1; self.drawlist.append(('bg', off, x, y, 64))

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
            if op & 1: self.use_video2 = True
            else: zoom = self.b()
        x = ((x & 0xFFFF) ^ 0x8000) - 0x8000
        y = ((y & 0xFFFF) ^ 0x8000) - 0x8000
        use2 = self.use_video2 and self.poly2
        self._pd = self.poly2.d if use2 else self.poly.d
        self.draw(off, s16(x), s16(y), zoom, 0xFF)
        bank = 2 if use2 else 1
        self.draws += 1; self.drawlist.append(('spr', off, x, y, zoom, bank))

    # ---- page ops at the LR size ----
    def op_fillpage(self):
        pg = self.page(self.b()); col = self.b()
        self.pages[pg][:] = bytes([col]) * LSIZE

    # ---- LR text: render op_drawstring through the INTRO's proven glyph blitter ----
    # The game text path mirrors the intro 1:1 (src/aw_text.asm == src_game/game_text.asm):
    # decompose each 8px font row into runs and emit them as 320-space spans so emit_span's
    # x>>1 LR mapping applies, exactly like the polygon path. draw_text is bound straight
    # from sim_atari.Sim above, so the GUI Atari panel matches the 6502 (and the intro)
    # byte-for-byte -- no separate full-width layout, no positional drift.
    def op_drawstring(self):
        strId = self.w(); x = self.b(); y = self.b(); color = self.b()
        self.draws += 1; self.drawlist.append(('txt', strId, x, y, color))
        self.draw_text(strId, x, y, color)


# GameAtari needs the GameVM OPS table (overridden op_memlist + LR op_fillpage/
# op_drawstring resolve through the instance, so reuse the table but repoint the
# two ops we changed).
GameAtari.OPS = list(game_sim.GameVM.OPS)
GameAtari.OPS[0x0E] = GameAtari.op_fillpage
GameAtari.OPS[0x12] = GameAtari.op_drawstring


def main():
    part = int(sys.argv[1]) if len(sys.argv) > 1 else 16002
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    vm = GameAtari(part)
    vm.run(n)
    print(f"part {part}: {len(vm.frames)} LR frames (160x200)")
    for idx in range(0, len(vm.frames), max(1, len(vm.frames) // 10)):
        page, pal, hold, draws, dl = vm.frames[idx]
        nz = sum(1 for c in page if c) * 100 // LSIZE
        print(f"  f{idx:4} pal={pal:2} draws={draws:3} non-empty={nz:3}%")


if __name__ == "__main__":
    main()
