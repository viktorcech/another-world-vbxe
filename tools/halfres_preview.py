import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import game_atari as G
import sim_atari as S

LW, H = S.LW, S.H

def render(part, frame):
    vm = G.GameAtari(part)
    vm.run(frame + 1)
    f = vm.frames[min(frame, len(vm.frames)-1)]
    return f[0], f[1], vm.pals      # use the GAME's own per-part palette, not intro

def chunky(page):
    """render-every-other-scanline + 2-tall spans: even row drives both rows."""
    out = bytearray(len(page))
    for y in range(H):
        src = (y // 2) * 2          # 0,0,2,2,4,4,...
        out[y*LW:(y+1)*LW] = page[src*LW:(src+1)*LW]
    return bytes(out)

def sidebyside(full, half, pal, path, scale=3):
    gap = 6
    w = LW*2 + gap
    rgb = bytearray(w*H*3)
    def blit(page, x0):
        for y in range(H):
            for i in range(LW):
                c = page[y*LW+i]
                r,g,b = pal[c] if c < 16 else (0,0,0)
                o = (y*w + x0 + i)*3
                rgb[o]=r; rgb[o+1]=g; rgb[o+2]=b
    blit(full, 0); blit(half, LW+gap)
    for y in range(H):
        for i in range(gap):
            o=(y*w+LW+i)*3; rgb[o]=rgb[o+1]=rgb[o+2]=40
    S.wpng(path, bytes(rgb), w, H, scale)

if __name__ == "__main__":
    part = int(sys.argv[1]) if len(sys.argv)>1 else 16005
    frame = int(sys.argv[2]) if len(sys.argv)>2 else 120
    page, palidx, pals = render(part, frame)
    pal = pals[palidx]
    full = page
    half = chunky(page)
    diff = sum(1 for a,b in zip(full,half) if a!=b)
    out = os.path.join(S.OUT, f'halfres_{part}_f{frame}.png')
    sidebyside(full, half, pal, out)
    print(f"part {part} frame {frame}: LEFT=full 160x200  RIGHT=half-res(100 lines x2)")
    print(f"  changed pixels by half-res: {diff}/{LW*H} ({diff*100//(LW*H)}%)  pal#{palidx}")
    print(f"  wrote {out}")
