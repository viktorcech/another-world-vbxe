#!/usr/bin/env python3
"""
game_gui.py - Another World GAME previewer (Tkinter, pure stdlib, Thonny-friendly).

The companion to gui.py (the intro previewer). The intro is deterministic, so its
GUI just scrubs pre-rendered frames. The GAME is INTERACTIVE -- the hero variables
come from a joystick every frame -- so this GUI plays the game_sim VM LIVE:

  * a PART picker (16002 water .. 16008 password) chooses the scene,
  * the KEYBOARD drives Lester in real time (arrows = move, Space = action),
  * each tick runs one VM scheduler pass (game_sim.GameVM.step) and shows the frame,
  * the scrubber replays the frames produced so far,
  * two panels (PC reference | ATARI LR 160) compare the ideal render and the VBXE
    low-res mode (1 byte = 2 hw pixels), like the intro GUI,
  * polygon IDs can be overlaid, and the report expands every shape (poly_desc /
    walk_parts) from the correct bank (video1 part shapes / video2 shared shapes),
  * "Save screenshot" bakes an 800x600 PNG with the polygon IDs, same as gui.py.

The faithful Atari LR *replay* panel (the 6502 game VM's own page) is added once
that VM exists; game_sim is its oracle.

Run from tools/:   python game_gui.py            # starts on the water part
                   python game_gui.py 16003      # start on the jail part
"""
import os, sys, json, shutil, subprocess, struct, zlib, copy
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import aw_sim
import game_sim
import game_atari

W, H = aw_sim.W, aw_sim.H
PAD = 6
GUI_CONFIG = os.path.join(HERE, '.gui_config.json')
SCRATCH = os.path.join(PROJ, 'out', '_view_game.ppm')
SHOTS = os.path.join(PROJ, 'out', 'shots')
SCREENS_DIR = os.path.join(PROJ, 'out', 'screens')      # auto-archived screen PNGs
ASM = os.path.join(PROJ, 'src_game', 'awgame.asm')
XEX = os.path.join(PROJ, 'awgame.xex')
ATR = os.path.join(PROJ, 'awgame.atr')                  # the game needs the ATR on D1:
GAME_VM = os.path.join(PROJ, 'src_game', 'game_vm.asm')  # holds GAME_START_PART
BUILD_PS1 = os.path.join(PROJ, 'build_awgame.ps1')       # 2-pass ATR build + guard


def set_start_part(part):
    """Patch `GAME_START_PART = NNNNN` in game_vm.asm so the game boots into `part`
    (woll3d's debug_spawn pattern). Returns the previous value to restore afterwards."""
    import re
    with open(GAME_VM, encoding='latin-1') as f:
        txt = f.read()
    m = re.search(r'GAME_START_PART\s*=\s*(\d+)', txt)
    prev = int(m.group(1)) if m else 16002
    txt = re.sub(r'(GAME_START_PART\s*=\s*)\d+', r'\g<1>%d' % part, txt, count=1)
    with open(GAME_VM, 'w', encoding='latin-1') as f:
        f.write(txt)
    return prev


def set_start_pos(pos):
    """Patch `GAME_START_POS = N` in game_vm.asm so the game boots straight into the
    AW VAR(0) checkpoint `pos` within the start part. Returns the previous value."""
    import re
    with open(GAME_VM, encoding='latin-1') as f:
        txt = f.read()
    m = re.search(r'GAME_START_POS\s*=\s*(\d+)', txt)
    prev = int(m.group(1)) if m else 0
    txt = re.sub(r'(GAME_START_POS\s*=\s*)\d+', r'\g<1>%d' % pos, txt, count=1)
    with open(GAME_VM, 'w', encoding='latin-1') as f:
        f.write(txt)
    return prev

# Jump targets, two groups:
#   * SCENES      -- the 9 raw parts, each started at its natural beginning (VAR0 = 0).
#   * CHECKPOINTS -- every ACCESS-CODE target, decoded straight from the part-16008
#                   bytecode. The code-compare chain at $073D tests the 4 entered
#                   symbols (VAR30..VAR33); on a match it does `VAR0 := pos` then
#                   op_memlist <part> -- i.e. AW's restartAt(part, pos), which just
#                   primes VAR(0)=pos before the part's thread 0 runs. 23 of the 24
#                   codes decode back to the on-screen string table; KRTD (-> 16007
#                   final) is the one code the wheel never shows (tools/dump_codes.py).
# The Atari game reaches these live by typing the code on the password screen; the GUI
# reproduces a checkpoint by priming var[0] right after constructing the VM (reset_part).
SCENES = [
    (16002, 'water  (gameplay start)'),
    (16003, 'jail'),
    (16004, 'cite'),
    (16005, 'arene'),
    (16006, 'luxe'),
    (16007, 'final'),
    (16001, 'intro  (cinematic)'),
    (16000, 'copy protection'),
    (16008, 'password screen'),
]

# (part, VAR0 pos, 4-letter code) -- sorted in gameplay-progression order (part, then
# checkpoint). Authoritative: extracted from the shipped part-16008 access-code bytecode.
CHECKPOINTS = [
    (16002, 10, 'LDKD'),
    (16003, 20, 'HTDC'),
    (16004, 30, 'CLLD'),
    (16004, 31, 'LBKG'),
    (16004, 33, 'XDDJ'),
    (16004, 35, 'FXLC'),
    (16004, 37, 'KRFK'),
    (16004, 39, 'KLFB'),
    (16004, 41, 'TTCT'),
    (16004, 42, 'DDRX'),
    (16004, 43, 'TBHK'),
    (16004, 44, 'BFLX'),
    (16004, 45, 'XJRT'),
    (16004, 46, 'HRTB'),
    (16004, 47, 'HBHK'),
    (16004, 48, 'JCGB'),
    (16004, 49, 'BRTD'),
    (16005, 50, 'CKJL'),
    (16006, 60, 'LFCK'),
    (16006, 62, 'HHFL'),
    (16006, 64, 'TFBB'),
    (16006, 66, 'TXHF'),
    (16006, 68, 'JHJL'),
    (16007, 70, 'KRTD'),
]

SCENE_NAME = {16000: 'copy protection', 16001: 'intro', 16002: 'water', 16003: 'jail',
              16004: 'cite', 16005: 'arene', 16006: 'luxe', 16007: 'final',
              16008: 'password'}

# JAIL (16003) rooms. Jail's rooms can't be reached by cold-starting the part -- they
# are gameplay-gated and need the post-water game state (proven: even the faithful
# another.js VM cold-started stalls at the opening). So we ACCESS a room by driving its
# own draw routine directly: the cutscene screens (1..5) via their per-screen blocks,
# the gameplay rooms via the room-setup at $8032. (room, bytecode_entry, label).
# The 6 gameplay rooms render distinctly via the room-setup at $8032. The opening
# cutscene (screens 1..5) is one cell view with only palette/camera changes the static
# force can't differentiate, so it is exposed as a single "cutscene" entry.
JAIL_ROOMS = [
    (1, 0x8894, 'cutscene (opening)'),
    (35, 0x8032, 'room 35'), (36, 0x8032, 'room 36'), (37, 0x8032, 'room 37'),
    (68, 0x8032, 'room 68'), (69, 0x8032, 'room 69'), (101, 0x8032, 'room 101'),
]

# unified picker list -- each entry is (part, pos, code, label); `label` is both the
# combobox text and the key we map back to (part, pos, code).
LOCATIONS = [(p, 0, '', f'{p}  {n}') for p, n in SCENES] + \
            [(p, pos, code, f'{p}  {code}  {SCENE_NAME[p]} +{pos}')
             for p, pos, code in CHECKPOINTS]
LOC_LABELS = [e[3] for e in LOCATIONS]
LOC_BY_LABEL = {e[3]: e for e in LOCATIONS}

# jail-room picker entries (label -> (room, entry_addr)), appended to the dropdown
JAILROOM_BY_LABEL = {f'16003  jail {name}': (room, entry) for room, entry, name in JAIL_ROOMS}
JAILROOM_LABELS = list(JAILROOM_BY_LABEL)

def loc_for_part(part):
    """The picker entry that starts `part` at its natural beginning (pos 0)."""
    for e in LOCATIONS:
        if e[0] == part and e[1] == 0:
            return e
    return LOCATIONS[0]


# --------------------------------------------------------------------------
# image helpers (live view via in-memory PPM; screenshots via PNG) -- same as gui.py
# --------------------------------------------------------------------------
def ppm_photo(rgb, w, h):
    os.makedirs(os.path.dirname(SCRATCH), exist_ok=True)
    with open(SCRATCH, 'wb') as f:
        f.write(b'P6\n%d %d\n255\n' % (w, h))
        f.write(rgb)
    return tk.PhotoImage(file=SCRATCH)


def combine(panels, scale, pad=PAD):
    """Stitch equal WxH RGB panels side by side, nearest-scale by `scale`."""
    n = len(panels)
    cw = W*n + pad*(n-1)
    out = bytearray(cw*H*3)
    for y in range(H):
        for i, p in enumerate(panels):
            s = y*W*3
            d = (y*cw + i*(W+pad))*3
            out[d:d+W*3] = p[s:s+W*3]
    if scale == 1:
        return bytes(out), cw, H
    rows = []
    for y in range(H):
        srow = out[y*cw*3:(y+1)*cw*3]
        big = bytearray()
        for x in range(cw):
            big.extend(srow[x*3:x*3+3]*scale)
        rows.extend([bytes(big)]*scale)
    return b''.join(rows), cw*scale, H*scale


def nearest_resize(rgb, w, h, nw, nh):
    out = bytearray(nw*nh*3)
    for y in range(nh):
        srow = rgb[(y*h//nh)*w*3:][:w*3]
        for x in range(nw):
            sx = (x*w//nw)*3
            out[(y*nw+x)*3:(y*nw+x)*3+3] = srow[sx:sx+3]
    return bytes(out)


def write_png(path, w, h, rgb):
    def chunk(typ, data):
        c = typ + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)))
        rows = b''.join(b'\x00' + rgb[y*w*3:(y+1)*w*3] for y in range(h))
        f.write(chunk(b'IDAT', zlib.compress(rows, 6)))
        f.write(chunk(b'IEND', b''))


# 3x5 bitmap font (digits + ':') for baking polygon labels into screenshots
GLYPHS = {
    '0': (0b111, 0b101, 0b101, 0b101, 0b111), '1': (0b010, 0b110, 0b010, 0b010, 0b111),
    '2': (0b111, 0b001, 0b111, 0b100, 0b111), '3': (0b111, 0b001, 0b111, 0b001, 0b111),
    '4': (0b101, 0b101, 0b111, 0b001, 0b001), '5': (0b111, 0b100, 0b111, 0b001, 0b111),
    '6': (0b111, 0b100, 0b111, 0b101, 0b111), '7': (0b111, 0b001, 0b010, 0b010, 0b010),
    '8': (0b111, 0b101, 0b111, 0b101, 0b111), '9': (0b111, 0b101, 0b111, 0b001, 0b111),
    ':': (0b000, 0b010, 0b000, 0b010, 0b000),
}


def draw_text(buf, cw, ch, x, y, s, color, sc=2):
    r, g, b = color
    for ch_ in s:
        gl = GLYPHS.get(ch_)
        if gl:
            for ry in range(5):
                row = gl[ry]
                for rx in range(3):
                    if row & (0b100 >> rx):
                        for dy in range(sc):
                            for dx in range(sc):
                                px = x + rx*sc + dx; py = y + ry*sc + dy
                                if 0 <= px < cw and 0 <= py < ch:
                                    o = (py*cw + px)*3
                                    buf[o] = r; buf[o+1] = g; buf[o+2] = b
        x += 4*sc


def _load_cfg():
    try: return json.load(open(GUI_CONFIG, encoding='utf-8'))
    except Exception: return {}
def _save_cfg(c):
    try: json.dump(c, open(GUI_CONFIG, 'w', encoding='utf-8'), indent=2)
    except Exception: pass
def find_tool(env, names, dirs, key):
    p = os.environ.get(env)
    if p and os.path.exists(p): return p
    p = _load_cfg().get(key)
    if p and os.path.exists(p): return p
    for d in dirs:
        for n in names:
            c = os.path.join(d, n)
            if os.path.exists(c): return c
    for n in names:
        w = shutil.which(n)
        if w: return w
    return None

MADS = find_tool('MADS', ('mads.exe', 'mads'), (PROJ,), 'mads')
_PF  = os.environ.get('ProgramFiles', r'C:\Program Files')
_PF6 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
ALTIRRA = find_tool('ALTIRRA', ('Altirra64.exe', 'Altirra.exe'),
                    (os.path.join(_PF, 'Altirra'), os.path.join(_PF6, 'Altirra'),
                     r'C:\Altirra', r'C:\atari\altirra', r'D:\Altirra',
                     r'D:\atari\altirra', r'D:\viktor\atari\altirra'), 'altirra')


# input-mask bits (game_sim.update_input): right=1 left=2 down=4 up/jump=8 action=0x80
KEY_BITS = {
    'Right': 1, 'Left': 2, 'Down': 4, 'Up': 8, 'space': 0x80, 'Return': 0x80,
    'd': 1, 'a': 2, 's': 4, 'w': 8,                # WASD alternative
}


# --------------------------------------------------------------------------
# polygon description / expansion (operate on a given bank's byte buffer)
# --------------------------------------------------------------------------
def poly_desc(d, off, color=0xFF):
    if not (0 <= off < len(d)):
        return 'off!'
    i = d[off]
    if i >= 0xC0:
        col = (i & 0x3F) if (color & 0x80) else color
        if off+3 < len(d):
            return f'fill col={col} bbox={d[off+1]}x{d[off+2]} v={d[off+3]}'
        return 'fill?'
    if (i & 0x3F) == 2 and off+3 < len(d):
        return f'GROUP of {d[off+3]+1} parts'
    return f'type={i & 0x3F}'


def walk_parts(d, off, x, y, zoom, color, depth, out):
    if not (0 <= off < len(d)) or len(out) >= 200:
        return
    i = d[off]
    if i >= 0xC0:
        col = (i & 0x3F) if (color & 0x80) else color
        out.append(f'{"  "*depth}id {off:>5}  fill col={col:<2} @({x},{y})')
    elif (i & 0x3F) == 2 and off+3 < len(d):
        bx = x - d[off+1]*zoom//64
        by = y - d[off+2]*zoom//64
        childs = d[off+3]
        out.append(f'{"  "*depth}id {off:>5}  GROUP @({x},{y}) -> {childs+1} parts:')
        p = off+4
        for _ in range(childs+1):
            if p+3 >= len(d) or len(out) >= 200:
                break
            word = (d[p] << 8) | d[p+1]; p += 2
            cx = bx + d[p]*zoom//64; p += 1
            cy = by + d[p]*zoom//64; p += 1
            ccol = 0xFF
            if word & 0x8000:
                ccol = d[p] & 0x7F; p += 2
            if depth < 4:
                walk_parts(d, (word & 0x7FFF)*2, cx, cy, zoom, ccol, depth+1, out)


def main():
    start_part = int(sys.argv[1]) if len(sys.argv) > 1 else 16002
    start_pos = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    # two VMs in lockstep with the SAME input: the 320 PC oracle (game_sim) and the
    # faithful 160-LR Atari render (game_atari). Identical bytecode decode keeps
    # their frame counts in step (verified: drawlists match frame-for-frame).
    vm = [game_sim.GameVM(start_part, 'int')]
    vmA = [game_atari.GameAtari(start_part)]
    vm[0].var[0] = start_pos; vmA[0].var[0] = start_pos   # AW VAR(0) = checkpoint
    rgbs = []                                     # PC-reference (320) rgb per frame
    argbs = []                                    # ATARI LR (160->320 doubled) rgb per frame
    held = set()                                  # currently pressed input bits

    # ---- screen gallery -----------------------------------------------------
    # AW tags every room with VAR_SCREEN_NUM (var 0x67). The engine has no jump to an
    # arbitrary screen -- you reach the mid-section rooms by PLAYING. So we watch 0x67
    # and the moment a NEW (part, screen) appears we deep-copy the live VM pair into a
    # snapshot. Play through (from any checkpoint) and the gallery fills with every
    # screen you visit; click one to teleport back to that exact live state and keep
    # playing. Over a session that builds a complete, navigable map of the game.
    VAR_SCREEN_NUM = 0x67
    screens = {}                                  # (part, screen_num) -> (vm, vmA, frame_idx)
    screen_keys = []                              # insertion order, parallel to the listbox

    def poly_d(bank):
        """The byte buffer for a draw-list bank (1=video1, 2=video2)."""
        if bank == 2 and vm[0].poly2:
            return vm[0].poly2.d
        return vm[0].poly.d

    def frame_rgb(idx):
        """PC rgb for produced frame idx, palette captured at production time."""
        while len(rgbs) < len(vm[0].frames):
            i = len(rgbs)
            pg, pal = vm[0].frames[i][0], vm[0].frames[i][1]
            rgbs.append(aw_sim.frame_to_rgb(pg, vm[0].pals[pal]))
        return rgbs[idx]

    def atari_rgb(idx):
        """ATARI LR rgb: the faithful 160-wide page, each column doubled to 320."""
        while len(argbs) < len(vmA[0].frames):
            i = len(argbs)
            page, pal = vmA[0].frames[i][0], vmA[0].frames[i][1]
            cols = vmA[0].pals[pal]
            out = bytearray(W * H * 3)
            for y in range(H):
                b = y * 160; o = y * W * 3
                for lx in range(160):
                    c = page[b + lx]
                    r, g, bl = cols[c] if c < 16 else (0, 0, 0)
                    out[o] = r; out[o+1] = g; out[o+2] = bl
                    out[o+3] = r; out[o+4] = g; out[o+5] = bl
                    o += 6
            argbs.append(bytes(out))
        idx = min(idx, len(argbs) - 1)
        return argbs[idx]

    state = {'idx': 0, 'playing': False, 'scale': 2, 'live': True, 'alive': True}

    root = tk.Tk()
    root.title('Another World GAME - VBXE previewer (live)')

    # ---------- left controls ----------
    left = tk.Frame(root); left.pack(side='left', padx=10, pady=10, anchor='n')

    tk.Label(left, text='Scene / access-code location', font=('Arial', 11, 'bold')).pack()
    _start = next((e for e in LOCATIONS if e[0] == start_part and e[1] == start_pos),
                  loc_for_part(start_part))
    part_var = tk.StringVar(value=_start[3])
    part_menu = ttk.Combobox(left, textvariable=part_var, state='readonly', width=30,
                             values=LOC_LABELS + JAILROOM_LABELS)
    part_menu.pack(pady=(0, 6))

    tk.Label(left, text='Frame', font=('Arial', 11, 'bold')).pack()
    fr_label = tk.Label(left, text=''); fr_label.pack()
    frame_scale = ttk.Scale(left, from_=0, to=1, orient='horizontal', length=320)
    frame_scale.pack()

    bb = tk.Frame(left); bb.pack(pady=6)
    play_btn = tk.Button(bb, text='Play', width=7)
    tk.Button(bb, text='|<', width=3, command=lambda: scrub_to(0)).pack(side='left')
    tk.Button(bb, text='<', width=3, command=lambda: scrub_to(state['idx']-1)).pack(side='left')
    play_btn.pack(side='left', padx=4)
    tk.Button(bb, text='>', width=3, command=lambda: scrub_to(state['idx']+1)).pack(side='left')
    tk.Button(bb, text='>|', width=3, command=lambda: scrub_to(len(vm[0].frames)-1)).pack(side='left')

    sb = tk.Frame(left); sb.pack()
    tk.Button(sb, text='Step pass', width=10, command=lambda: do_step()).pack(side='left', padx=2)
    tk.Button(sb, text='Reset part', width=10, command=lambda: reset_part()).pack(side='left', padx=2)

    tk.Label(left, text='Zoom').pack(pady=(10, 0))
    zoom_var = tk.IntVar(value=state['scale'])
    zb = tk.Frame(left); zb.pack()
    def on_zoom(): state['scale'] = zoom_var.get(); show()
    for z in (1, 2, 3):
        tk.Radiobutton(zb, text=f'{z}x', variable=zoom_var, value=z,
                       command=on_zoom).pack(side='left')

    panels_var = tk.StringVar(value='2')
    pb = tk.Frame(left); pb.pack(pady=(8, 0))
    tk.Label(pb, text='Panels:').pack(side='left')
    tk.Radiobutton(pb, text='PC only', variable=panels_var, value='1',
                   command=lambda: show()).pack(side='left')
    tk.Radiobutton(pb, text='PC | ATARI LR', variable=panels_var, value='2',
                   command=lambda: show()).pack(side='left')

    ids_var = tk.BooleanVar(value=False)
    tk.Checkbutton(left, text='Show polygon IDs (bg=green spr=red)',
                   variable=ids_var, command=lambda: show()).pack(pady=(6, 0))

    tk.Label(left, text='Controls (click view first):\n'
                       '  Arrows / WASD = move & jump\n'
                       '  Space / Enter = action',
             justify='left', fg='#333').pack(pady=(10, 0))
    inp_label = tk.Label(left, text='input: --', fg='#06c', font=('Courier New', 10))
    inp_label.pack(pady=(2, 0))

    status = tk.Label(left, text='', fg='#080', wraplength=320, justify='left')
    def set_status(m, ok=True): status.config(text=m, fg=('#080' if ok else '#b00'))

    altirra = [ALTIRRA]
    def build_and_run():
        """TEST IN ALTIRRA: build the ATR booting straight into the SELECTED scene,
        then launch Altirra with it on D1: (woll3d pattern). The game streams its
        parts from the ATR, so we boot the .atr (not the bare .xex)."""
        if not MADS:
            set_status('mads.exe not found in project root', ok=False); return
        if not altirra[0]:
            altirra[0] = filedialog.askopenfilename(
                title='Select Altirra64.exe',
                filetypes=[('Altirra', 'Altirra*.exe'), ('exe', '*.exe')])
            if not altirra[0]:
                set_status('Altirra path required', ok=False); return
            c = _load_cfg(); c['altirra'] = altirra[0]; _save_cfg(c)
        sel = part_var.get()
        jailroom = JAILROOM_BY_LABEL.get(sel)
        if jailroom:
            # A jail room is a GUI-only forced view (we drive its draw routine in the sim).
            # The real engine can't BOOT into a mid-jail room -- it's gameplay-gated -- so
            # Altirra boots jail at its start; the room itself stays preview-only.
            part, pos, code = 16003, 0, ''
        else:
            part, pos, code, _lbl = LOC_BY_LABEL.get(sel, LOCATIONS[0])
        name = SCENE_NAME.get(part, '')
        # Boot straight into the selected checkpoint: patch GAME_START_PART (which part)
        # AND GAME_START_POS (the AW VAR(0) checkpoint) so the booted game lands exactly
        # where the GUI preview shows -- same restartAt(part,pos) the code screen does.
        tgt = f'part {part} ({name})' + (f' code {code} +{pos}' if code else '')
        if jailroom:
            tgt += f' [jail room {jailroom[0]} is GUI-preview-only; Altirra boots jail start]'
        set_status(f'Building ATR booting into {tgt} ...'); root.update()
        prev_part = set_start_part(part)                  # patch GAME_START_PART
        prev_pos = set_start_pos(pos)                     # patch GAME_START_POS
        try:
            r = subprocess.run(['powershell', '-ExecutionPolicy', 'Bypass', '-File', BUILD_PS1],
                               cwd=PROJ, capture_output=True, text=True)
        finally:
            set_start_part(prev_part)                     # restore -> repo build stays default
            set_start_pos(prev_pos)
        if r.returncode != 0 or not os.path.exists(ATR):
            set_status('BUILD FAILED (see console)', ok=False)
            print(r.stdout, r.stderr); return
        try:
            subprocess.Popen([altirra[0], ATR])           # mount awgame.atr as D1: + boot
            set_status(f'launched Altirra -> {tgt}')
        except OSError as e:
            set_status(f'launch failed: {e}', ok=False)

    def save_screenshot():
        i = state['idx']
        if not vm[0].frames:
            return
        panels = [frame_rgb(i)]
        if panels_var.get() == '2':
            panels.append(atari_rgb(i))           # faithful 160-LR Atari render
        rgb, w, h = combine(panels, 1)
        cw, ch = 800, 600
        scl = min(cw/w, ch/h)
        nw, nh = int(w*scl), int(h*scl)
        rz = nearest_resize(rgb, w, h, nw, nh)
        canv = bytearray(cw*ch*3)
        ox, oy = (cw-nw)//2, (ch-nh)//2
        for y in range(nh):
            d = ((oy+y)*cw + ox)*3
            canv[d:d+nw*3] = rz[y*nw*3:(y+1)*nw*3]
        # bake the polygon IDs onto the right-most (ATARI LR) panel
        dl = vm[0].frames[i][4]
        panel_x = (W + PAD) if panels_var.get() == '2' else 0
        for n, ent in enumerate(dl):
            kind, off, x, yy = ent[0], ent[1], ent[2], ent[3]
            if 0 <= x < W and 0 <= yy < H:
                px = ox + int((panel_x + x) * scl)
                py = oy + int(yy * scl)
                col = (0, 255, 0) if kind == 'bg' else (255, 90, 90)
                draw_text(canv, cw, ch, px, py, f'{n}:{off}', col, 2)
        os.makedirs(SHOTS, exist_ok=True)
        p = os.path.join(SHOTS, f'game_{vm[0].cur_part}_f{i:04d}.png')
        write_png(p, cw, ch, bytes(canv))
        set_status(f'saved {p}'); print('screenshot:', p)

    tk.Button(left, text='Save screenshot (800x600)', command=save_screenshot,
              bg='#2d6cdf', fg='white', font=('Arial', 10, 'bold')).pack(pady=(14, 2))
    tk.Button(left, text='TEST IN ALTIRRA (selected scene)', command=build_and_run,
              bg='#cc3333', fg='white', font=('Arial', 10, 'bold')).pack(pady=2)
    status.pack(pady=(4, 0))

    # ---------- screen gallery : every room visited; click to teleport back ----------
    gal = tk.Frame(root); gal.pack(side='left', padx=(0, 6), pady=10, anchor='n')
    tk.Label(gal, text='Screens visited', font=('Arial', 11, 'bold')).pack()
    screen_count = tk.Label(gal, text='0 captured', fg='#06c')
    screen_count.pack()
    slb = tk.Frame(gal); slb.pack()
    screen_sb = tk.Scrollbar(slb, orient='vertical')
    screen_list = tk.Listbox(slb, width=16, height=26, font=('Courier New', 9),
                             yscrollcommand=screen_sb.set, exportselection=False)
    screen_sb.config(command=screen_list.yview)
    screen_list.pack(side='left'); screen_sb.pack(side='left', fill='y')
    tk.Label(gal, text='Scan = auto-capture every\nVAR(0) entry screen of the\nselected part. Play/walk to\nreach the rest. Click an\nentry to jump back.\nAll saved to out/screens/.',
             fg='#555', justify='left', font=('Arial', 8)).pack(pady=(4, 0))
    tk.Button(gal, text='Scan entries', width=12, bg='#2d8c4a', fg='white',
              font=('Arial', 9, 'bold'), command=lambda: scan_entries()).pack(pady=(4, 0))
    tk.Button(gal, text='Clear', width=12,
              command=lambda: clear_screens()).pack(pady=(2, 0))

    # ---------- right: view + report ----------
    right = tk.Frame(root); right.pack(side='left', padx=10, pady=10)
    hdr = tk.Label(right, text='PC reference (game_sim VM - the 6502 oracle)',
                   font=('Arial', 10, 'bold'))
    hdr.pack()
    view = tk.Canvas(right, bg='black', highlightthickness=0,
                     width=W*2, height=H*2, takefocus=1)
    view.pack()
    view.focus_set()
    img_holder = [None]

    rhdr = tk.Frame(right); rhdr.pack(fill='x', pady=(8, 0))
    tk.Label(rhdr, text='VM state + draw-list (shapes expanded; ID = byte offset)',
             font=('Arial', 9)).pack(side='left')
    tk.Button(rhdr, text='Copy to clipboard',
              command=lambda: copy_report()).pack(side='right')
    report = scrolledtext.ScrolledText(right, width=80, height=13,
                                        font=('Courier New', 9))
    report.pack()

    def copy_report():
        txt = report.get('1.0', 'end-1c')
        try:
            root.clipboard_clear(); root.clipboard_append(txt); root.update()
        except tk.TclError:
            pass
        fp = os.path.join(PROJ, 'out', 'game_draw_list.txt')
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        open(fp, 'w', encoding='utf-8').write(txt)
        set_status(f'copied to clipboard + saved {fp}')

    # ---------- input handling ----------
    def refresh_input():
        m = 0
        for b in held:
            m |= b
        vm[0].input = m; vmA[0].input = m         # both VMs see the same joystick
        names = '+'.join(sorted(k for k, v in KEY_BITS.items()
                                if v in held and len(k) > 1)) or '--'
        inp_label.config(text=f'input: {names}')

    def on_key(ev, down):
        b = KEY_BITS.get(ev.keysym)
        if b is None:
            return
        if down: held.add(b)
        else: held.discard(b)
        refresh_input()
    view.bind('<KeyPress>', lambda e: on_key(e, True))
    view.bind('<KeyRelease>', lambda e: on_key(e, False))
    view.bind('<Button-1>', lambda e: view.focus_set())

    # ---------- screen capture / teleport ----------
    def save_screen_png(sv, key):
        """Archive a captured screen as a clean 320x200 PNG (out/screens/)."""
        try:
            pg, pal = sv.frames[-1][0], sv.frames[-1][1]
            rgb = aw_sim.frame_to_rgb(pg, sv.pals[pal])
            os.makedirs(SCREENS_DIR, exist_ok=True)
            write_png(os.path.join(SCREENS_DIR, f'{key[0]}_scr{key[1]:03d}.png'), W, H, rgb)
        except Exception:
            pass

    def _record(sv, sa):
        """Snapshot (sv, sa) the first time each (part, screen_num) is seen; archive PNG.
        The screen_num is set BEFORE the room's background is composed, so we step
        throwaway copies forward (neutral input, same screen) until the backdrop is
        drawn -- then both the teleport snapshot and the PNG show the real screen."""
        if not sv.frames:
            return False
        key = (sv.cur_part, sv.var[VAR_SCREEN_NUM] & 0xFFFF)
        if key in screens:
            return False
        cv, ca = copy.deepcopy(sv), copy.deepcopy(sa)
        cv.input = 0; ca.input = 0
        for _ in range(18):                         # let the background compose
            if not cv.running or (cv.cur_part, cv.var[VAR_SCREEN_NUM] & 0xFFFF) != key:
                break
            cv.step()
            if ca.running: ca.step()
        screens[key] = (cv, ca, len(cv.frames)-1)    # mark seen (don't reprocess)
        # skip pure transition frames (a near-single-colour, contentless page). The page
        # is W*H colour-index bytes; if one index covers ~all of it there's no room drawn.
        try:
            buf = cv.frames[-1][0]
            dom = max(buf.count(v) for v in set(buf)) if buf else 0
            if dom > 0.985 * len(buf):
                return False
        except Exception:
            pass
        screen_keys.append(key)
        screen_list.insert('end', f'{key[0]}  scr {key[1]}')
        screen_count.config(text=f'{len(screen_keys)} captured')
        save_screen_png(cv, key)
        return True

    def capture_screens():
        """Log any newly-entered room from the live VM pair."""
        _record(vm[0], vmA[0])

    def scan_entries():
        """Hands-free: run every VAR(0) entry of the SELECTED part and capture each
        distinct screen the engine can reach without playing. Saves PNGs + snapshots."""
        part = LOC_BY_LABEL.get(part_var.get(), LOCATIONS[0])[0]
        positions = sorted({0} | {pos for p, pos, c in CHECKPOINTS if p == part})
        if state['playing']: toggle()
        before = len(screen_keys)
        for n, pos in enumerate(positions):
            set_status(f'scanning part {part}: entry {n+1}/{len(positions)} (pos {pos})...')
            root.update()
            sv = game_sim.GameVM(part, 'int'); sv.var[0] = pos
            sa = game_atari.GameAtari(part); sa.var[0] = pos
            for _ in range(90):
                if sv.running: sv.step()
                if sa.running: sa.step()
                _record(sv, sa)
        set_status(f'scan done: +{len(screen_keys)-before} new screens '
                   f'({len(screen_keys)} total, PNGs in out/screens/)')

    def goto_screen(evt=None):
        sel = screen_list.curselection()
        if not sel:
            return
        key = screen_keys[sel[0]]
        svm, svmA, _ = screens[key]
        if state['playing']: toggle()
        vm[0] = copy.deepcopy(svm); vmA[0] = copy.deepcopy(svmA)   # restore a pristine copy
        rgbs.clear(); argbs.clear(); held.clear()
        state['idx'] = max(0, len(vm[0].frames)-1); state['live'] = True
        frame_scale.config(to=max(1, len(vm[0].frames)-1)); frame_scale.set(state['idx'])
        refresh_input(); show()
        set_status(f'teleported to part {key[0]} screen {key[1]}')
    screen_list.bind('<<ListboxSelect>>', goto_screen)

    def clear_screens():
        screens.clear(); screen_keys.clear()
        screen_list.delete(0, 'end')
        screen_count.config(text='0 captured')

    def force_jail_room(v, room, entry):
        """Access a jail room by driving its own draw routine: prime a little (load
        palette/resources), prime the room number, then point thread 0 at the room's
        draw entry and let the install-ed draw threads compose the background."""
        cutscene = entry != 0x8032
        for _ in range(4 if cutscene else 60):
            if v.running: v.step()
        v.var[103] = room; v.var[16] = room; v.var[17] = 0; v.var[10] = 0; v.var[102] = 0xFFFF
        v.tpc = [game_sim.INACTIVE] * 64; v.tpc[0] = entry
        v.tpause = [0] * 64; v.treq = [game_sim.NO_REQ] * 64; v.tpause_req = [0xFF] * 64
        for _ in range(40 if cutscene else 110):
            if v.running: v.step()
            else: break

    # ---------- VM advance / display ----------
    def do_step():
        refresh_input()
        produced = vm[0].step()
        if vmA[0].running:
            vmA[0].step()                          # keep the Atari render in lockstep
        if produced:
            state['idx'] = len(vm[0].frames) - 1
        capture_screens()                          # log any newly-entered room
        frame_scale.config(to=max(1, len(vm[0].frames)-1))
        show()
        return produced

    def scrub_to(i):
        if state['playing']: toggle()
        state['idx'] = max(0, min(len(vm[0].frames)-1, i))
        state['live'] = (state['idx'] == len(vm[0].frames)-1)
        frame_scale.set(state['idx']); show()

    def reset_part():
        sel = part_var.get()
        if state['playing']: toggle()
        clear_screens()                             # new location -> fresh gallery
        rgbs.clear(); argbs.clear(); held.clear()
        if sel in JAILROOM_BY_LABEL:                # ACCESS a jail room via its draw routine
            room, entry = JAILROOM_BY_LABEL[sel]
            vm[0] = game_sim.GameVM(16003, 'int'); vm[0].var[0] = 30
            vmA[0] = game_atari.GameAtari(16003); vmA[0].var[0] = 30
            force_jail_room(vm[0], room, entry)
            force_jail_room(vmA[0], room, entry)
            label = sel
        else:
            part, pos, code, label = LOC_BY_LABEL.get(sel, LOCATIONS[0])
            vm[0] = game_sim.GameVM(part, 'int')
            vmA[0] = game_atari.GameAtari(part)
            vm[0].var[0] = pos; vmA[0].var[0] = pos  # AW restartAt(part, pos): prime VAR(0)
            for _ in range(8):
                if vm[0].running: vm[0].step()
                if vmA[0].running: vmA[0].step()
        capture_screens()                           # log the room
        state['idx'] = max(0, len(vm[0].frames)-1); state['live'] = True
        frame_scale.config(to=max(1, len(vm[0].frames)-1)); frame_scale.set(state['idx'])
        refresh_input(); show()
        set_status(f'reset to {label}')
    part_menu.bind('<<ComboboxSelected>>', lambda e: reset_part())

    def show():
        i = state['idx']; sc = state['scale']
        if not vm[0].frames:
            return
        i = max(0, min(len(vm[0].frames)-1, i))
        panels = [frame_rgb(i)]
        if panels_var.get() == '2':
            panels.append(atari_rgb(i))           # faithful 160-LR Atari render
            hdr.config(text='PC reference (ideal)        |        ATARI LOW (faithful VBXE LR 160)')
        else:
            hdr.config(text='PC reference (game_sim VM - the 6502 oracle)')
        rgb, w, h = combine(panels, sc)
        img = ppm_photo(rgb, w, h)
        view.config(width=w, height=h)
        view.delete('all'); view.create_image(0, 0, anchor='nw', image=img)
        img_holder[0] = img
        pg, pal, hold, draws, dl = vm[0].frames[i]
        live = ' [LIVE]' if (state['live'] and i == len(vm[0].frames)-1) else ''
        fr_label.config(text=f'{i} / {len(vm[0].frames)-1}{live}')
        # polygon-ID overlay on the right-most panel
        if ids_var.get():
            ax0 = (W + PAD) * sc if panels_var.get() == '2' else 0
            for n, ent in enumerate(dl):
                kind, x, y = ent[0], ent[2], ent[3]
                if 0 <= x < W and 0 <= y < H:
                    col = '#00ff00' if kind == 'bg' else '#ff5050'
                    cx = ax0 + x*sc; cy = y*sc
                    view.create_text(cx+1, cy+1, text=f'{n}:{ent[1]}',
                                     fill='black', font=('Arial', 8, 'bold'))
                    view.create_text(cx, cy, text=f'{n}:{ent[1]}',
                                     fill=col, font=('Arial', 8, 'bold'))
        act = [t for t in range(64) if vm[0].tpc[t] != aw_sim.INACTIVE and not vm[0].tpause[t]]
        report.delete('1.0', tk.END)
        report.insert(tk.END, f'part {vm[0].cur_part}   frame {i}   palette {pal}   '
                              f'hold {hold}   {draws} polygons   running={vm[0].running}\n')
        report.insert(tk.END, f'active threads ({len(act)}): {act}\n')
        report.insert(tk.END, '='*62 + '\n')
        for n, ent in enumerate(dl):
            kind, off, x, y, zoom = ent[0], ent[1], ent[2], ent[3], ent[4]
            bank = ent[5] if len(ent) > 5 else 1
            d = poly_d(bank)
            tag = {1: 'v1', 2: 'v2'}[bank]
            report.insert(tk.END, f'#{n}  {kind}[{tag}]  id {off}  @({x},{y}) zoom {zoom}  '
                                  f'{poly_desc(d, off)}\n')
            parts = []
            walk_parts(d, off, x, y, zoom, 0xFF, 1, parts)
            for line in parts:
                report.insert(tk.END, line + '\n')

    def on_scale(v):
        idx = int(float(v))
        if idx != state['idx']:
            state['live'] = (idx == len(vm[0].frames)-1)
            state['idx'] = idx; show()
    frame_scale.config(command=on_scale)

    # ---------- live play loop (guarded against the window being destroyed) ----------
    def tick():
        if not state['alive'] or not state['playing']:
            return
        try:
            if not root.winfo_exists():
                return
        except tk.TclError:
            return
        if vm[0].running:
            do_step()
        else:
            toggle()                          # reached the end -> stop playing
            return
        root.after(60, tick)                  # ~16 fps (Python render-bound)
    def toggle():
        state['playing'] = not state['playing']
        play_btn.config(text='Pause' if state['playing'] else 'Play')
        if state['playing']:
            state['live'] = True
            view.focus_set()
            tick()
    play_btn.config(command=toggle)

    def on_close():
        state['playing'] = False; state['alive'] = False
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', on_close)

    # prime a few frames (both VMs) so there's something to show, then idle
    for _ in range(8):
        if vm[0].running: vm[0].step()
        if vmA[0].running: vmA[0].step()
    capture_screens()                              # log the start room
    state['idx'] = max(0, len(vm[0].frames)-1)
    frame_scale.config(to=max(1, len(vm[0].frames)-1)); frame_scale.set(state['idx'])
    refresh_input(); show()
    root.mainloop()


if __name__ == '__main__':
    main()
