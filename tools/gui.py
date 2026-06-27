#!/usr/bin/env python3
"""
gui.py - Another World intro previewer (Tkinter, pure stdlib, Thonny-friendly).

Same shape as woll3d/tools/gui.py: a left control column and a right view with
side-by-side panels + a report, and a TEST IN ALTIRRA button. Here the scrubber
walks the intro frames produced by aw_sim's VM (the PC reference) and the panels
show IDEAL | ATARI SR (320) | ATARI LR (160) so you can compare the VBXE modes.

No image files are written: the live view is fed to Tk as an in-memory PPM.

Run from tools/:   python gui.py
"""
import os, sys, base64, shutil, subprocess, json, re, struct, zlib
from collections import OrderedDict
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import aw_sim
import sim_atari

W, H = aw_sim.W, aw_sim.H
GUI_CONFIG = os.path.join(HERE, '.gui_config.json')
ASM = os.path.join(PROJ, 'src', 'awvbxe.asm')
XEX = os.path.join(PROJ, 'awintro.xex')
PAD = 6


# --------------------------------------------------------------------------
# in-memory image helpers (no files written)
# --------------------------------------------------------------------------
SCRATCH = os.path.join(PROJ, 'out', '_view.ppm')   # reused display buffer (PPM, not PNG)
SHOTS = os.path.join(PROJ, 'out', 'shots')

def ppm_photo(rgb, w, h):
    """Tk reliably loads a PPM *file* (the data= path is unsupported in many
    Tk builds -> blank canvas). Write one reused P6 scratch file and load it."""
    os.makedirs(os.path.dirname(SCRATCH), exist_ok=True)
    with open(SCRATCH, 'wb') as f:
        f.write(b'P6\n%d %d\n255\n' % (w, h))
        f.write(rgb)
    return tk.PhotoImage(file=SCRATCH)


def lr_sim(rgb):
    """VBXE LR: 1 byte = 2 hw px. Keep even columns, double them back."""
    out = bytearray(W*H*3)
    for y in range(H):
        row = rgb[y*W*3:(y+1)*W*3]; d = y*W*3
        for x in range(0, W, 2):
            px = row[x*3:x*3+3]
            out[d:d+3] = px; out[d+3:d+6] = px; d += 6
    return bytes(out)


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
    sw = cw*scale
    rows = []
    for y in range(H):
        srow = out[y*cw*3:(y+1)*cw*3]
        big = bytearray()
        for x in range(cw):
            big.extend(srow[x*3:x*3+3]*scale)
        big = bytes(big)
        rows.extend([big]*scale)
    return b''.join(rows), sw, H*scale


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


def nearest_resize(rgb, w, h, nw, nh):
    out = bytearray(nw*nh*3)
    for y in range(nh):
        srow = rgb[(y*h//nh)*w*3:][:w*3]
        for x in range(nw):
            sx = (x*w//nw)*3
            out[(y*nw+x)*3:(y*nw+x)*3+3] = srow[sx:sx+3]
    return bytes(out)


def fit_canvas(rgb, w, h, cw, ch):
    """Aspect-fit `rgb` into a cw x ch black canvas (centred)."""
    sc = min(cw/w, ch/h)
    nw, nh = max(1, int(w*sc)), max(1, int(h*sc))
    rz = nearest_resize(rgb, w, h, nw, nh)
    canv = bytearray(cw*ch*3)
    ox, oy = (cw-nw)//2, (ch-nh)//2
    for y in range(nh):
        d = ((oy+y)*cw + ox)*3
        canv[d:d+nw*3] = rz[y*nw*3:(y+1)*nw*3]
    return bytes(canv)


# 3x5 bitmap font (digits + ':') for baking polygon labels into screenshots
GLYPHS = {
    '0': (0b111, 0b101, 0b101, 0b101, 0b111),
    '1': (0b010, 0b110, 0b010, 0b010, 0b111),
    '2': (0b111, 0b001, 0b111, 0b100, 0b111),
    '3': (0b111, 0b001, 0b111, 0b001, 0b111),
    '4': (0b101, 0b101, 0b111, 0b001, 0b001),
    '5': (0b111, 0b100, 0b111, 0b001, 0b111),
    '6': (0b111, 0b100, 0b111, 0b101, 0b111),
    '7': (0b111, 0b001, 0b010, 0b010, 0b010),
    '8': (0b111, 0b101, 0b111, 0b101, 0b111),
    '9': (0b111, 0b101, 0b111, 0b001, 0b111),
    ':': (0b000, 0b010, 0b000, 0b010, 0b000),
}


def draw_text(buf, cw, ch, x, y, s, color, sc=2):
    """Bake string `s` (digits/':') into RGB buffer at (x,y), block-scaled."""
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
        x += 4*sc                                   # 3 px glyph + 1 px gap


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


def main():
    # Default: the WHOLE intro. The VM stops itself at the part switch (~2613
    # frames) so a big cap just means "all". Pass an arg to cap for quick tests.
    maxf = int(sys.argv[1]) if len(sys.argv) > 1 else 100000
    # The VM raster is cheap (~0.9 s for the full intro per pass); the slow part
    # is frame_to_rgb (~12 ms/frame). So run both VM passes fully up front, but
    # convert to RGB LAZILY on display (LRU-cached) -> startup ~2 s, not ~60 s.
    print('Running the intro VM, float (IDEAL) pass ...')
    frames, pals = aw_sim.render_intro(maxf, 'float')
    # ATARI panel: the FAITHFUL 6502 LR replay (160-wide page, x>>1 emit). This is
    # what the Atari actually shows -- NOT lr_sim (keep-even-columns), which drops
    # thin features on odd columns (e.g. the 1px elevator-descent strip at x=121).
    print('Running the faithful 6502 LR replay (ATARI panel) ...')
    sa = sim_atari.Sim(); sa.run(maxf)
    poly_bytes = aw_sim.load()[2]                 # cinematic poly data for decoding
    print(f'{len(frames)} frames ready (RGB converted on demand).')

    # lazy RGB cache: convert only the frames actually viewed, keep a bounded LRU
    _rgb_cache = OrderedDict()                     # (src_id, i) -> rgb bytes
    RGB_CACHE_MAX = 128
    def rgb_ideal(i):
        key = (0, i)
        hit = _rgb_cache.get(key)
        if hit is not None:
            _rgb_cache.move_to_end(key); return hit
        pg, pal = frames[i][0], frames[i][1]
        rgb = aw_sim.frame_to_rgb(pg, pals[pal])
        _rgb_cache[key] = rgb
        if len(_rgb_cache) > RGB_CACHE_MAX:
            _rgb_cache.popitem(last=False)
        return rgb
    def rgb_atari(i):
        # sim_atari page is 160-wide LR; double each column back to 320 for display
        key = (1, i)
        hit = _rgb_cache.get(key)
        if hit is not None:
            _rgb_cache.move_to_end(key); return hit
        page, pal = sa.frames[i]; cols = pals[pal]
        out = bytearray(W * H * 3)
        for y in range(H):
            b = y * 160; o = y * W * 3
            for lx in range(160):
                c = page[b + lx]
                r, g, bl = cols[c] if c < 16 else (0, 0, 0)
                out[o] = r; out[o+1] = g; out[o+2] = bl
                out[o+3] = r; out[o+4] = g; out[o+5] = bl
                o += 6
        rgb = bytes(out)
        _rgb_cache[key] = rgb
        if len(_rgb_cache) > RGB_CACHE_MAX:
            _rgb_cache.popitem(last=False)
        return rgb

    def poly_desc(off, color=0xFF):
        """One-line description of the shape at `off`."""
        d = poly_bytes
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

    def walk_parts(off, x, y, zoom, color, depth, out):
        """Enumerate every polygon (and sub-polygon) under `off` with its ID,
        so a dropout of ANY part on the Atari is identifiable. Appends text
        lines to `out`."""
        d = poly_bytes
        if not (0 <= off < len(d)) or len(out) >= 200:
            return
        i = d[off]
        if i >= 0xC0:                                  # leaf shape
            col = (i & 0x3F) if (color & 0x80) else color
            out.append(f'{"  "*depth}id {off:>5}  fill col={col:<2} @({x},{y})')
        elif (i & 0x3F) == 2 and off+3 < len(d):       # group of parts
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
                    walk_parts((word & 0x7FFF)*2, cx, cy, zoom, ccol, depth+1, out)

    state = {'idx': 0, 'playing': False, 'scale': 1, 'panels': '3'}

    root = tk.Tk()
    root.title('Another World intro - VBXE previewer')

    # ---------- left controls ----------
    left = tk.Frame(root); left.pack(side='left', padx=10, pady=10, anchor='n')

    tk.Label(left, text='Frame', font=('Arial', 11, 'bold')).pack()
    fr_label = tk.Label(left, text='')
    fr_label.pack()
    frame_scale = ttk.Scale(left, from_=0, to=len(frames)-1, orient='horizontal',
                            length=320)
    frame_scale.pack()

    bb = tk.Frame(left); bb.pack(pady=6)
    def step(d):
        state['idx'] = max(0, min(len(frames)-1, state['idx']+d))
        frame_scale.set(state['idx']); show()
    tk.Button(bb, text='|<', width=3, command=lambda: step(-10**9)).pack(side='left')
    tk.Button(bb, text='<', width=3, command=lambda: step(-1)).pack(side='left')
    play_btn = tk.Button(bb, text='Play', width=6); play_btn.pack(side='left', padx=4)
    tk.Button(bb, text='>', width=3, command=lambda: step(1)).pack(side='left')
    tk.Button(bb, text='>|', width=3, command=lambda: step(10**9)).pack(side='left')

    tk.Label(left, text='Zoom').pack(pady=(10, 0))
    zoom_var = tk.IntVar(value=1)
    zb = tk.Frame(left); zb.pack()
    def on_zoom():
        state['scale'] = zoom_var.get(); show()
    for z in (1, 2, 3):
        tk.Radiobutton(zb, text=f'{z}x', variable=zoom_var, value=z,
                       command=on_zoom).pack(side='left')

    ids_var = tk.BooleanVar(value=False)
    tk.Checkbutton(left, text='Show polygon IDs (#:off, bg=green spr=red)',
                   variable=ids_var, command=lambda: show()).pack(pady=(10, 0))

    tk.Label(left, text='Build mode for Altirra').pack(pady=(10, 0))
    mode_var = tk.StringVar(value='LR')
    mb = tk.Frame(left); mb.pack()
    tk.Radiobutton(mb, text='SR 320', variable=mode_var, value='SR').pack(side='left')
    tk.Radiobutton(mb, text='LR 160', variable=mode_var, value='LR').pack(side='left')

    status = tk.Label(left, text='', fg='#080', wraplength=320, justify='left')

    def set_status(m, ok=True): status.config(text=m, fg=('#080' if ok else '#b00'))

    altirra = [ALTIRRA]
    def build_and_run():
        if not MADS:
            set_status('mads.exe not found in project root', ok=False); return
        try:
            txt = open(ASM, encoding='ascii').read()
            val = '1' if mode_var.get() == 'LR' else '0'
            txt = re.sub(r'(?m)^(LORES\s+equ\s+)\d+', r'\g<1>'+val, txt)
            open(ASM, 'w', encoding='ascii').write(txt)
        except Exception as e:
            set_status(f'patch failed: {e}', ok=False); return
        set_status(f'Building {mode_var.get()} ...'); root.update()
        r = subprocess.run([MADS, ASM, f'-o:{XEX}'], cwd=PROJ,
                           capture_output=True, text=True)
        if r.returncode != 0:
            set_status('BUILD FAILED (see console)', ok=False)
            print(r.stdout, r.stderr); return
        if not altirra[0]:
            altirra[0] = filedialog.askopenfilename(
                title='Select Altirra64.exe',
                filetypes=[('Altirra', 'Altirra*.exe'), ('exe', '*.exe')])
            if not altirra[0]:
                set_status('Altirra path required', ok=False); return
            c = _load_cfg(); c['altirra'] = altirra[0]; _save_cfg(c)
        try:
            subprocess.Popen([altirra[0], XEX])
            set_status(f'launched Altirra ({mode_var.get()})')
        except OSError as e:
            set_status(f'launch failed: {e}', ok=False)

    def save_screenshot():
        i = state['idx']
        rgb, w, h = combine([rgb_ideal(i), rgb_atari(i)], 1)   # 646x200
        cw, ch = 800, 600
        scl = min(cw/w, ch/h)
        nw, nh = int(w*scl), int(h*scl)
        rz = nearest_resize(rgb, w, h, nw, nh)
        canv = bytearray(cw*ch*3)
        ox, oy = (cw-nw)//2, (ch-nh)//2
        for y in range(nh):
            d = ((oy+y)*cw + ox)*3
            canv[d:d+nw*3] = rz[y*nw*3:(y+1)*nw*3]
        # bake the polygon IDs onto the ATARI panel so the screenshot records them
        dl = frames[i][4]
        for n, (kind, off, x, yy, zoom) in enumerate(dl):
            if 0 <= x < W and 0 <= yy < H:
                px = ox + int((W + PAD + x) * scl)
                py = oy + int(yy * scl)
                col = (0, 255, 0) if kind == 'bg' else (255, 90, 90)
                draw_text(canv, cw, ch, px, py, f'{n}:{off}', col, 2)
        os.makedirs(SHOTS, exist_ok=True)
        p = os.path.join(SHOTS, f'frame{i:04d}.png')
        write_png(p, cw, ch, bytes(canv))
        set_status(f'saved {p}')
        print('screenshot:', p)

    tk.Button(left, text='Save screenshot (800x600)', command=save_screenshot,
              bg='#2d6cdf', fg='white', font=('Arial', 10, 'bold')).pack(pady=(14, 2))
    tk.Button(left, text='TEST IN ALTIRRA', command=build_and_run,
              bg='#cc3333', fg='white', font=('Arial', 10, 'bold')).pack(pady=2)
    status.pack(pady=(4, 0))

    # ---------- right: view + report ----------
    right = tk.Frame(root); right.pack(side='left', padx=10, pady=10)
    tk.Label(right, text='PC (reference)          |          ATARI LOW (VBXE LR 160)',
             font=('Arial', 10, 'bold')).pack()
    view = tk.Canvas(right, bg='black', highlightthickness=0)
    view.pack()
    img_holder = [None]

    rhdr = tk.Frame(right); rhdr.pack(fill='x', pady=(8, 0))
    tk.Label(rhdr, text='Draw list for this frame (the polygons the VM emits)',
             font=('Arial', 9)).pack(side='left')
    tk.Button(rhdr, text='Copy to clipboard',
              command=lambda: copy_report()).pack(side='right')
    report = scrolledtext.ScrolledText(right, width=80, height=12,
                                        font=('Courier New', 9))
    report.pack()

    def copy_report():
        txt = report.get('1.0', 'end-1c')
        try:
            root.clipboard_clear()
            root.clipboard_append(txt)
            root.update()
        except tk.TclError:
            pass
        # always also write a file fallback (reliable regardless of clipboard)
        fp = os.path.join(PROJ, 'out', 'draw_list.txt')
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        open(fp, 'w', encoding='utf-8').write(txt)
        set_status(f'copied to clipboard + saved {fp}')

    def show():
        i = state['idx']; sc = state['scale']
        ideal = rgb_ideal(i)                         # float PC reference
        panels = [ideal, rgb_atari(i)]               # PC | ATARI LOW (faithful LR)
        rgb, w, h = combine(panels, sc)
        img = ppm_photo(rgb, w, h)
        view.config(width=w, height=h)
        view.delete('all'); view.create_image(0, 0, anchor='nw', image=img)
        img_holder[0] = img
        pg, pal, hold, draws, dl = frames[i]         # draw-list (same VM for both)
        fr_label.config(text=f'{i} / {len(frames)-1}')
        # overlay each polygon's index on the ATARI LOW panel at its (x,y)
        if ids_var.get():
            ax0 = (W + PAD) * sc                      # ATARI panel left edge
            for n, (kind, off, x, y, zoom) in enumerate(dl):
                if 0 <= x < W and 0 <= y < H:
                    col = '#00ff00' if kind == 'bg' else '#ff5050'   # bg / spr
                    cx = ax0 + x*sc; cy = y*sc
                    view.create_text(cx+1, cy+1, text=f'{n}:{off}',  # black halo
                                     fill='black', font=('Arial', 8, 'bold'))
                    view.create_text(cx, cy, text=f'{n}:{off}',
                                     fill=col, font=('Arial', 8, 'bold'))
        report.delete('1.0', tk.END)
        report.insert(tk.END, f'frame {i}   palette {pal}   hold {hold} '
                              f'host-frames   {draws} polygons\n')
        report.insert(tk.END, 'ID = byte offset into the poly data = the stable '
                              'name. If a polygon is missing on the Atari, find\n'
                              'its ID here. Groups are expanded so a missing PART '
                              'is identifiable too.\n')
        report.insert(tk.END, '='*62 + '\n')
        for n, (kind, off, x, y, zoom) in enumerate(dl):
            report.insert(tk.END,
                f'#{n}  {kind}  id {off}  @({x},{y}) zoom {zoom}  '
                f'{poly_desc(off)}\n')
            parts = []
            walk_parts(off, x, y, zoom, 0xFF, 1, parts)
            for line in parts:
                report.insert(tk.END, line + '\n')

    def on_scale(v): state['idx'] = int(float(v)); show()
    frame_scale.config(command=on_scale)

    def tick():
        if state['playing']:
            state['idx'] = (state['idx']+1) % len(frames)
            frame_scale.set(state['idx']); show()
            root.after(80, tick)
    def toggle():
        state['playing'] = not state['playing']
        play_btn.config(text='Pause' if state['playing'] else 'Play')
        if state['playing']: tick()
    play_btn.config(command=toggle)

    show()
    root.mainloop()


if __name__ == '__main__':
    main()
