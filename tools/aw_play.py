#!/usr/bin/env python3
"""
aw_play.py - replay the flattened intro from ONLY the exported .bin files.

Reads out/intro_playlist.bin + out/intro_poly.bin + out/intro_pal.bin and
reproduces the intro with no VM, no threads -- exactly the loop the 6502 will
run. Used to verify the playlist export is self-contained and correct.

    python aw_play.py            # replay, verify against the VM, print stats
"""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aw_sim
import aw_text
from aw_sim import PolyData, fill_poly_int, W, H, SIZE

OUT = os.path.join(os.path.dirname(HERE), 'out')


def load_pal_bin(path):
    d = open(path, 'rb').read()                 # 32 * 16 * (r,g,b), 7-bit
    pals = []
    for k in range(32):
        cols = [(d[k*48+i*3] << 1, d[k*48+i*3+1] << 1, d[k*48+i*3+2] << 1)
                for i in range(16)]
        pals.append(cols)
    return pals


class Player:
    def __init__(self):
        self.pl = open(os.path.join(OUT, 'intro_playlist.bin'), 'rb').read()
        self.poly = PolyData(open(os.path.join(OUT, 'intro_poly.bin'), 'rb').read(),
                             fill_poly_int)
        self.pals = load_pal_bin(os.path.join(OUT, 'intro_pal.bin'))
        self.pages = [bytearray(SIZE) for _ in range(4)]
        self.cur1 = 2
        self.curpal = 0
        self.frames = []
        self.p = 0

    def u8(self):  v = self.pl[self.p]; self.p += 1; return v
    def u16(self): v = struct.unpack_from('<H', self.pl, self.p)[0]; self.p += 2; return v
    def s16(self): v = struct.unpack_from('<h', self.pl, self.p)[0]; self.p += 2; return v

    def run(self):
        while True:
            op = self.u8()
            if op == 0x00:                      # END
                break
            elif op == 0x01:                    # SETPAL
                self.curpal = self.u8()
            elif op == 0x02:                    # SELPAGE
                self.cur1 = self.u8()
            elif op == 0x03:                    # FILLPAGE
                pg = self.u8(); col = self.u8()
                self.pages[pg][:] = bytes([col]) * SIZE
            elif op == 0x04:                    # COPYPAGE
                s = self.u8(); d = self.u8()
                self.pages[d][:] = self.pages[s][:]
            elif op == 0x05:                    # DRAWPOLY
                off = self.u16(); x = self.s16(); y = self.s16(); zoom = self.s16()
                self.poly.page0 = self.pages[0]
                self.poly.draw(self.pages[self.cur1], off, x, y, zoom, 0xFF)
            elif op == 0x07:                    # DRAWTEXT
                strId = self.u16(); x = self.u8(); y = self.u8(); col = self.u8()
                aw_text.draw_string(self.pages[self.cur1], W, strId, x, y, col)
            elif op == 0x08:                    # SOUND (idx) -- no audio here; consume operand
                self.u8()
            elif op == 0x06:                    # BLIT
                pg = self.u8(); hold = self.u8()
                self.frames.append((bytes(self.pages[pg]), self.curpal, hold))
        return self.frames


def main():
    pl = Player()
    frames = pl.run()
    print(f'replayed frames: {len(frames)}')

    # verify against the VM integer raster (must be identical)
    vm_frames, _ = aw_sim.render_intro(len(frames) + 5, 'int')
    n = min(len(frames), len(vm_frames))
    mism = 0
    for i in range(n):
        if frames[i][0] != vm_frames[i][0] or frames[i][1] != vm_frames[i][1]:
            mism += 1
    print(f'compared {n} frames vs VM   mismatches: {mism}')
    print('OK - playlist reproduces the VM exactly' if mism == 0
          else 'DIFFERENCES - playlist/replayer not faithful')


if __name__ == '__main__':
    main()
