#!/usr/bin/env python3
"""dump_jail_screens.py - render every JAIL (part 16003) screen/room in BOTH the PC
oracle (game_sim, 320) and the faithful ATARI LR (game_atari, 160->320) renderers,
side by side, so LR graphics glitches can be located per room.

Jail's rooms can't be reached by cold-starting the part (the rooms are gameplay-gated
and need the post-water game state). So instead we drive each room's own draw routine
directly: the cutscene screens (1..5) via their per-screen blocks, the gameplay rooms
(35,36,37,68,69,101) via the room-setup at $8032. Each is forced by priming
VAR_SCREEN_NUM (0x67) + VAR(0x10) and pointing a thread at the routine, then letting
the install-ed draw threads compose the background. Output: out/screens/jail_<room>.png
(PC | ATARI LR) and a contact sheet. Run from tools/:  python dump_jail_screens.py
"""
import os, hashlib
import game_sim, game_atari, aw_sim

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
OUT = os.path.join(PROJ, 'out', 'screens')
W, H = aw_sim.W, aw_sim.H
PAD = 6
INACT = game_sim.INACTIVE
VAR_SCREEN_NUM = 0x67

# room -> bytecode entry that draws it. Cutscene screens use their per-screen blocks;
# gameplay rooms use the shared room-setup at $8032 (installs the room draw threads).
CUTSCENE = {1: 0x8894, 2: 0x88b9, 3: 0x88de, 4: 0x8903, 5: 0x8928}
GAMEPLAY = [35, 36, 37, 68, 69, 101]


def _write_png(path, w, h, rgb):
    import struct, zlib
    def chunk(t, d): c = t + d; return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)))
        rows = b''.join(b'\x00' + rgb[y*w*3:(y+1)*w*3] for y in range(h))
        f.write(chunk(b'IDAT', zlib.compress(rows, 6)))
        f.write(chunk(b'IEND', b''))


def atari_rgb(vmA):
    """The faithful 160-wide LR page, each column doubled to 320 (same as the GUI)."""
    page, pal = vmA.frames[-1][0], vmA.frames[-1][1]
    cols = vmA.pals[pal]
    out = bytearray(W * H * 3)
    for y in range(H):
        b = y * 160; o = y * W * 3
        for lx in range(160):
            c = page[b + lx]
            r, g, bl = cols[c] if c < 16 else (0, 0, 0)
            out[o] = r; out[o+1] = g; out[o+2] = bl
            out[o+3] = r; out[o+4] = g; out[o+5] = bl
            o += 6
    return bytes(out)


def force_room(vm, room, entry, run=110):
    """Prime the room number + point thread 0 at its draw routine; let the install-ed
    draw threads compose the background, then return the last frame's rgb (PC) or page."""
    for _ in range(60):
        if vm.running: vm.step()
    vm.var[103] = room; vm.var[16] = room; vm.var[17] = 0; vm.var[10] = 0; vm.var[102] = 0xFFFF
    vm.tpc = [INACT] * 64; vm.tpc[0] = entry; vm.tpause = [0] * 64
    for _ in range(run):
        if vm.running: vm.step()
        else: break


def combine(pc, lr):
    cw = W * 2 + PAD
    out = bytearray(cw * H * 3)
    for y in range(H):
        s = y * W * 3
        out[(y*cw)*3:(y*cw)*3 + W*3] = pc[s:s+W*3]
        d = (y*cw + W + PAD) * 3
        out[d:d + W*3] = lr[s:s+W*3]
    return bytes(out), cw


def main():
    os.makedirs(OUT, exist_ok=True)
    rooms = sorted(CUTSCENE) + GAMEPLAY
    seen = {}
    for room in rooms:
        entry = CUTSCENE.get(room, 0x8032)
        vm = game_sim.GameVM(16003, 'int'); vm.var[0] = 30
        vmA = game_atari.GameAtari(16003); vmA.var[0] = 30
        force_room(vm, room, entry)
        force_room(vmA, room, entry)
        if not vm.frames or not vmA.frames:
            print(f"room {room:3}: no frame"); continue
        pg, pal = vm.frames[-1][0], vm.frames[-1][1]
        pc = aw_sim.frame_to_rgb(pg, vm.pals[pal])
        lr = atari_rgb(vmA)
        h = hashlib.md5(pc).hexdigest()[:8]
        dup = ' (dup of %d)' % seen[h] if h in seen else ''
        seen.setdefault(h, room)
        rgb, cw = combine(pc, lr)
        _write_png(os.path.join(OUT, f'jail_{room:03d}.png'), cw, H, rgb)
        print(f"room {room:3}: {h}{dup}  -> jail_{room:03d}.png  (left=PC oracle | right=ATARI LR)")
    print(f"\n{len(rooms)} jail rooms rendered to {os.path.normpath(OUT)} "
          f"({len(set(seen))} visually distinct).")


if __name__ == '__main__':
    main()
