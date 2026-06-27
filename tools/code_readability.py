import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim_atari as S, aw_text, game_atari as G
LW, H = S.LW, S.H
_orig = G.GameAtari.draw_text

def crisp(self, str_id, x, y, color):
    s = aw_text.STRINGS.get(str_id)
    if s is None: return
    page = self.pages[self.cur]; col = 0
    for ch in s:
        if ch == '\n': y += 8; col = 0; continue
        oc = ord(ch)
        if 0x20 <= oc <= 0x7F:
            g = (oc - 0x20) * 8
            base = x * 2 + col * 8        # half start scale + 8 LR px/glyph -> crisp AND fits
            for j in range(8):
                row = aw_text.FONT[g + j]; py = y + j
                if 0 <= py < H:
                    for i in range(8):
                        if (row & (0x80 >> i)) and 0 <= base+i < LW and color < 0x10:
                            page[py * LW + base + i] = color
        col += 1

def render(frame, use_crisp):
    G.GameAtari.draw_text = crisp if use_crisp else _orig
    vm = G.GameAtari(16008); vm.run(frame + 1)
    f = vm.frames[min(frame, len(vm.frames)-1)]
    return f[0], f[1], vm.pals

def wpng21(path, pg, pal, sx=4, sy=2):    # 2:1 hardware aspect (LR px = 2x wide)
    W = LW*sx; Ht = H*sy
    raw = bytearray()
    import struct, zlib
    for yy in range(Ht):
        raw.append(0); ry = yy//sy
        for xx in range(W):
            c = pg[ry*LW + xx//sx]; r,gg,bb = pal[c] if c<16 else (0,0,0)
            raw += bytes((r,gg,bb))
    def ch(t,d):
        c=t+d; return struct.pack('>I',len(d))+c+struct.pack('>I',zlib.crc32(c)&0xffffffff)
    open(path,'wb').write(b'\x89PNG\r\n\x1a\n'+ch(b'IHDR',struct.pack('>IIBBBBB',W,Ht,8,2,0,0,0))+ch(b'IDAT',zlib.compress(bytes(raw),9))+ch(b'IEND',b''))

if __name__=="__main__":
    frame=int(sys.argv[1]) if len(sys.argv)>1 else 20
    cur,pi,pals=render(frame,False)
    cri,_,_=render(frame,True)
    wpng21(os.path.join(S.OUT,f"code_cur_{frame}.png"), cur, pals[pi])
    wpng21(os.path.join(S.OUT,f"code_crisp_{frame}.png"), cri, pals[pi])
    print(f"frame {frame}: out/code_cur_{frame}.png (current 4px) + out/code_crisp_{frame}.png (crisp 8px), 2:1 aspect")
