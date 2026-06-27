#!/usr/bin/env python3
"""
sim_atari.py - a FAITHFUL Python model of src/awvbxe.asm's renderer.

Unlike aw_sim (the algorithm oracle) this mirrors what the 6502 ACTUALLY does:
4 LR pages (160x200), the exact poly decode, the 320-space 16.16 raster, the
emit_span x>>1 LR mapping, fill_span's 3 colour modes, and the playlist opcode
handling. It replays out/intro_playlist.bin + intro_poly.bin exactly like the
Atari, so a divergence from the GUI's ATARI-LOW oracle (lr_sim of the 320 render)
localises a PORT bug that the 320-only diffs miss (pages, fill_span, LR).

    python tools/sim_atari.py [frame]      # default 132 ; dumps diffs vs oracle
"""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aw_sim
import aw_text

OUT = os.path.join(os.path.dirname(HERE), 'out')
LW, H = 160, 200          # LR page is 160 wide
W320 = 320


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class Sim:
    def __init__(self):
        self.poly = open(os.path.join(OUT, 'intro_poly.bin'), 'rb').read()
        self.pl = open(os.path.join(OUT, 'intro_playlist.bin'), 'rb').read()
        self.pages = [bytearray(LW * H) for _ in range(4)]
        self.cur = 2
        self.curpal = 0
        self.frames = []          # (bytes(page), pal)
        self.p = 0

    # ---- poly data + zoom ----
    def by(self, off):
        return self.poly[off & 0xFFFF]

    def mul(self, m, zoom):
        return (m * zoom) >> 6     # asm: (m*zoom)>>6

    # ---- decoder (mirrors poly_draw / do_fill / do_hier) ----
    def draw(self, off, x, y, zoom, col):
        i = self.by(off); off += 1
        if i >= 0xC0:
            c = (i & 0x3F) if (col & 0x80) else col
            self.fill(off, c, zoom, x, y)
        elif (i & 0x3F) == 2:
            self.hier(off, zoom, x, y, col)

    def fill(self, off, color, zoom, ptx, pty):
        bbw = self.mul(self.by(off), zoom); off += 1
        bbh = self.mul(self.by(off), zoom); off += 1
        n = self.by(off); off += 1
        x0 = s16(ptx - (bbw >> 1))
        y0 = s16(pty - (bbh >> 1))
        pts = []
        for _ in range(n):
            px = s16(x0 + self.mul(self.by(off), zoom)); off += 1
            py = s16(y0 + self.mul(self.by(off), zoom)); off += 1
            pts.append((px, py))
        self.fill_poly_int(pts, color)

    def hier(self, off, zoom, ptx, pty, color):
        bx = s16(ptx - self.mul(self.by(off), zoom)); off += 1
        by = s16(pty - self.mul(self.by(off), zoom)); off += 1
        childs = self.by(off); off += 1
        for _ in range(childs + 1):
            word = (self.by(off) << 8) | self.by(off + 1); off += 2
            cx = s16(bx + self.mul(self.by(off), zoom)); off += 1
            cy = s16(by + self.mul(self.by(off), zoom)); off += 1
            ccol = 0xFF
            if word & 0x8000:
                ccol = self.by(off) & 0x7F; off += 2
            self.draw((word & 0x7FFF) * 2, cx, cy, zoom, ccol)

    # ---- edge slope: reciprocal LUT + 8x16 multiply (mirrors the asm) ----
    _recip = [0] * 256
    for _dy in range(2, 256):
        _recip[_dy] = round(65536 / _dy)

    @staticmethod
    def calc_step(dv, hh):
        sign = dv < 0
        ad = abs(dv) & 0xFF                 # |dx| < 256 (8-bit multiply)
        if hh == 1:
            m = ad << 16
        else:
            m = ad * Sim._recip[hh & 0xFF]  # |dx| * round(65536/dy)
        return (-m) & 0xFFFFFFFF if sign else (m & 0xFFFFFFFF)

    # ---- 16.16 raster (mirrors fill_poly_int) + LR emit ----
    def fill_poly_int(self, pts, color):
        n = len(pts)
        if n < 3:
            for (x, y) in pts:
                if 0 <= y < H and 0 <= x < W320:
                    self.span(y, x, x, color)
            return
        i = 0; j = n - 1
        hy = pts[0][1]
        cr = (pts[j][0] & 0xFFFF) << 16
        cl = (pts[i][0] & 0xFFFF) << 16
        i += 1; j -= 1
        numv = n
        while True:
            numv -= 2
            if numv == 0:
                return
            h = pts[i][1] - pts[i - 1][1]
            dvr = pts[j][0] - pts[j + 1][0]
            dvl = pts[i][0] - pts[i - 1][0]
            hh = (h & 0xFF) if h > 0 else 1
            sr = self.calc_step(dvr, hh)
            sl = self.calc_step(dvl, hh)
            i += 1; j -= 1
            cr = (cr & 0xFFFF0000) | 0x7FFF
            cl = (cl & 0xFFFF0000) | 0x8000
            if h == 0:
                cr = (cr + sr) & 0xFFFFFFFF
                cl = (cl + sl) & 0xFFFFFFFF
                continue
            if h < 0:
                continue
            for _ in range(h & 0xFF):
                if 0 <= hy < H:
                    xr = s16((cr >> 16) & 0xFFFF)
                    xl = s16((cl >> 16) & 0xFFFF)
                    a, b = (xl, xr) if xl <= xr else (xr, xl)
                    if a <= W320 - 1 and b >= 0:
                        a = max(0, a); b = min(W320 - 1, b)
                        self.span(hy, a, b, color)
                cr = (cr + sr) & 0xFFFFFFFF
                cl = (cl + sl) & 0xFFFFFFFF
                hy += 1
                if hy > H - 1:
                    return

    # ---- emit_span (LR x>>1) + fill_span (3 colour modes, 160-wide) ----
    def span(self, y, a, b, color):
        bx_a = a >> 1
        bx_b = b >> 1
        ln = bx_b - bx_a + 1
        self.fill_span(bx_a, y, ln, color)

    def fill_span(self, sx, sy, ln, color):
        page = self.pages[self.cur]
        off = sy * LW + sx
        if color < 0x10:
            for k in range(ln):
                page[off + k] = color
        elif color == 0x10:
            for k in range(ln):
                page[off + k] |= 0x08
        else:
            p0 = self.pages[0]
            for k in range(ln):
                page[off + k] = p0[off + k]

    # ---- text: ONE 4x8 BLT_BSTENCIL blit per glyph from the pre-expanded VRAM
    #      font (mirrors the 6502 rewrite: font_init makes each LR byte $FF iff
    #      either of its 2 pixels is set; the blit writes (src AND col) and skips
    #      src==0, so a byte is coloured iff >=1 of its pixels is set -- the same
    #      coverage the old per-run floor(x/2) emit_span path produced). HEIGHT
    #      is clamped per line (rows-1 = min(7, 199-y)); a line at y >= 200 is
    #      hidden; a glyph at column >= 40 is skipped. ----
    def draw_text(self, str_id, x, y, color):
        s = aw_text.STRINGS.get(str_id)
        if s is None:
            return
        cx = x
        vis = 0 <= y < H
        gh = min(7, H - 1 - y) if vis else 0
        for ch in s:
            if ch == '\n':
                y += 8; cx = x
                vis = 0 <= y < H
                gh = min(7, H - 1 - y) if vis else 0
                continue
            oc = ord(ch)
            if 0x20 <= oc <= 0x7F and vis and cx < 40:
                g = (oc - 0x20) * 8
                page = self.pages[self.cur]
                for r in range(gh + 1):
                    row = aw_text.FONT[g + r]
                    off = (y + r) * LW + cx * 4
                    for j in range(4):
                        if row & (0xC0 >> (2 * j)):
                            page[off + j] = color
            cx += 1

    # ---- playlist opcodes ----
    def u8(self):
        v = self.pl[self.p]; self.p += 1; return v

    def u16(self):
        v = self.pl[self.p] | (self.pl[self.p + 1] << 8); self.p += 2; return v

    def s16r(self):
        return s16(self.u16())

    def run(self, want_frame):
        while True:
            op = self.u8()
            if op == 0x00:
                break
            elif op == 0x01:
                self.curpal = self.u8()
            elif op == 0x02:
                self.cur = self.u8()
            elif op == 0x03:
                pg = self.u8(); col = self.u8()
                self.pages[pg][:] = bytes([col]) * (LW * H)
            elif op == 0x04:
                s = self.u8(); d = self.u8()
                self.pages[d][:] = self.pages[s][:]
            elif op == 0x05:
                off = self.u16(); x = self.s16r(); y = self.s16r(); z = self.s16r()
                self.draw(off, x, y, z, 0xFF)
            elif op == 0x07:
                str_id = self.u16(); x = self.u8(); y = self.u8(); col = self.u8()
                self.draw_text(str_id, x, y, col)
            elif op == 0x08:
                self.u8()                       # SOUND idx (1 byte) -- no audio in the sim,
                                                # but the operand MUST be consumed or the
                                                # playlist stream desyncs (was: 5 frames only)
            elif op == 0x06:
                pg = self.u8(); hold = self.u8()
                self.frames.append((bytes(self.pages[pg]), self.curpal))
                if len(self.frames) > want_frame:
                    return


def load_pal_bin(path):
    d = open(path, 'rb').read()
    pals = []
    for k in range(32):
        cols = [(d[k*48+i*3] << 1, d[k*48+i*3+1] << 1, d[k*48+i*3+2] << 1)
                for i in range(16)]
        pals.append(cols)
    return pals


def wpng(path, rgb, w, h, scale=2):
    import struct, zlib
    W = w*scale; H = h*scale
    raw = bytearray()
    for y in range(H):
        raw.append(0)
        row = rgb[(y//scale)*w*3:(y//scale+1)*w*3]
        for x in range(w):
            raw += row[x*3:x*3+3]*scale
    def ch(t, d):
        c = t+d
        return struct.pack('>I', len(d))+c+struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' +
        ch(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 2, 0, 0, 0)) +
        ch(b'IDAT', zlib.compress(bytes(raw), 9)) + ch(b'IEND', b''))


def page_to_rgb(page, pal):
    out = bytearray(LW*H*3)
    for i in range(LW*H):
        c = page[i]
        o = i*3
        r, g, b = pal[c] if c < 16 else (0, 0, 0)
        out[o] = r; out[o+1] = g; out[o+2] = b
    return bytes(out)


def lr_oracle(page320, pal):
    """The GUI ATARI-LOW model: render 320, keep EVEN columns -> 160 LR bytes."""
    out = bytearray(LW * H)
    for y in range(H):
        for i in range(LW):
            out[y * LW + i] = page320[y * W320 + (i * 2)]
    return out


def main():
    fi = int(sys.argv[1]) if len(sys.argv) > 1 else 132
    sim = Sim()
    sim.run(fi)
    my_page, my_pal = sim.frames[fi]

    # oracle: aw_sim int frame -> keep even columns
    of, pals = aw_sim.render_intro(fi + 2, 'int')
    o_page320, o_pal, *_ = of[fi]
    o_lr = lr_oracle(o_page320, o_pal)

    diff = sum(1 for a, b in zip(my_page, o_lr) if a != b)
    print(f'frame {fi}: my_pal={my_pal} oracle_pal={o_pal}  '
          f'differing LR bytes: {diff} / {LW*H}')

    # render MY sim's page to a PNG (this is exactly what the asm logic produces)
    pals = load_pal_bin(os.path.join(OUT, 'intro_pal.bin'))
    rgb = page_to_rgb(my_page, pals[my_pal])
    out = os.path.join(OUT, f'sim{fi}.png')
    wpng(out, rgb, LW, H, 3)
    print(f'  wrote {out}  (my sim_atari render of frame {fi})')
    # per-row diff histogram (where do they differ?)
    rows = {}
    for y in range(H):
        c = sum(1 for x in range(LW) if my_page[y*LW+x] != o_lr[y*LW+x])
        if c:
            rows[y] = c
    if rows:
        ys = sorted(rows)
        print(f'  differing rows: {ys[0]}..{ys[-1]}  ({len(rows)} rows)')
        # show a few sample columns on the worst row
        wy = max(rows, key=rows.get)
        print(f'  worst row {wy} ({rows[wy]} diffs):')
        mine = [my_page[wy*LW+x] for x in range(LW)]
        orac = [o_lr[wy*LW+x] for x in range(LW)]
        print('    mine  :', mine[40:110])
        print('    oracle:', orac[40:110])


if __name__ == '__main__':
    main()
