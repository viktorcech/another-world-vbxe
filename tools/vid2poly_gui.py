#!/usr/bin/env python3
"""
vid2poly_gui.py - interactive previewer for the MP4 -> AW-polygon conversion.

Same shape as gui.py (left controls + right side-by-side panels, PPM display via
a reused scratch file). Scrub through the video at a low preview fps and tune the
conversion live:

    ORIGINAL (1:1)  |  QUANTIZED (<=N colours)  |  AW-POLYGON RENDER

Sliders: frame, colours, eps (Douglas-Peucker), min-area, median despeckle.
The polygon panel is rendered THROUGH aw_sim.fill_poly_int, so it is exactly
what the Atari/VBXE raster would draw. The title bar shows the polygon count
(compare it to the original intro: median 3, max 56 polys/frame!).

Run from project root:   python tools/vid2poly_gui.py
                         python tools/vid2poly_gui.py --fps 12 --video video/x.mp4
"""
import os, sys, argparse
import tkinter as tk
from tkinter import ttk
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import vid2poly as v2p
from vid2poly import W, H

SCRATCH = os.path.join(PROJ, 'out', '_v2p_view.ppm')


def ppm_photo(img):
    """Tk reliably loads a PPM *file* (data= path is blank in many Tk builds)."""
    os.makedirs(os.path.dirname(SCRATCH), exist_ok=True)
    img.convert('RGB').save(SCRATCH, format='PPM')
    return tk.PhotoImage(file=SCRATCH)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--video', default=v2p.VIDEO)
    ap.add_argument('--fps', type=float, default=12.0,
                    help='preview/sample fps (how the frame slider is decimated)')
    args = ap.parse_args()

    rd = v2p.open_video(args.video)
    meta = rd.get_meta_data()
    src_fps = meta.get('fps', 24.0)
    duration = meta.get('duration', 60.0)
    nframes = max(1, int(duration * args.fps))
    print(f'video: {meta.get("size")}  {src_fps:.2f} fps src  {duration:.1f}s '
          f'-> {nframes} preview frames @ {args.fps} fps')

    # frame cache (decoded source frames are expensive to seek)
    _fcache = {}

    def get_frame(i):
        if i not in _fcache:
            sec = i / args.fps
            _fcache[i] = rd.get_data(int(round(sec * src_fps)))
            if len(_fcache) > 64:
                _fcache.pop(next(iter(_fcache)))
        return _fcache[i]

    state = {'idx': 0, 'scale': 3, 'playing': False}

    root = tk.Tk()
    root.title('vid2poly - MP4 -> Another World polygons')

    # ---------------- left controls ----------------
    left = tk.Frame(root)
    left.pack(side='left', padx=10, pady=10, anchor='n')

    tk.Label(left, text='Frame', font=('Arial', 11, 'bold')).pack()
    fr_label = tk.Label(left, text='')
    fr_label.pack()
    frame_scale = ttk.Scale(left, from_=0, to=nframes - 1, orient='horizontal', length=300)
    frame_scale.pack()

    bb = tk.Frame(left); bb.pack(pady=6)

    def step(d):
        state['idx'] = max(0, min(nframes - 1, state['idx'] + d))
        frame_scale.set(state['idx']); show()

    tk.Button(bb, text='|<', width=3, command=lambda: step(-10**9)).pack(side='left')
    tk.Button(bb, text='<', width=3, command=lambda: step(-1)).pack(side='left')
    play_btn = tk.Button(bb, text='Play', width=6); play_btn.pack(side='left', padx=4)
    tk.Button(bb, text='>', width=3, command=lambda: step(1)).pack(side='left')
    tk.Button(bb, text='>|', width=3, command=lambda: step(10**9)).pack(side='left')

    # parameter sliders ----------------------------------------------------
    def mk_slider(label, lo, hi, init, res=1):
        tk.Label(left, text=label, font=('Arial', 10, 'bold')).pack(pady=(12, 0))
        val = tk.Label(left, text=str(init)); val.pack()
        s = ttk.Scale(left, from_=lo, to=hi, orient='horizontal', length=300)
        s.set(init)
        s.pack()
        return s, val

    colors_s, colors_v = mk_slider('Colours', 2, 16, 16)
    desp_s, desp_v     = mk_slider('despeckle (cut polys, no holes)', 0, 60, 12)
    eps_s, eps_v       = mk_slider('eps (simplify)', 0.0, 6.0, 2.0)
    area_s, area_v     = mk_slider('min-area (keep low!)', 1, 60, 4)
    med_s, med_v       = mk_slider('median (0=off)', 0, 9, 0)

    info = tk.Label(left, text='', font=('Consolas', 10), justify='left')
    info.pack(pady=(14, 0))

    tk.Label(left, text='ORIGINAL | QUANTIZED | POLYGONS',
             font=('Arial', 9), fg='#666').pack(pady=(14, 0))

    # ---------------- right view ----------------
    right = tk.Frame(root); right.pack(side='left', padx=10, pady=10)
    canvas = tk.Label(right); canvas.pack()
    photo = {'img': None}

    def compute():
        i = state['idx']
        rgb = get_frame(i)
        ncolors = int(round(colors_s.get()))
        despeckle = int(round(desp_s.get()))
        eps = round(eps_s.get(), 2)
        min_area = int(round(area_s.get()))
        median = int(round(med_s.get()))

        orig = v2p.letterbox(rgb)
        idx, pal = v2p.quantize_frame(rgb, ncolors, 0.0, median)
        idx = v2p.despeckle(idx, despeckle)
        counts = np.bincount(idx.reshape(-1), minlength=16)
        bg = int(counts.argmax())
        polys = v2p.vectorise(idx, eps, min_area, bg)
        out_rgb = v2p.render(polys, pal, bg)

        quant = v2p.idx_to_img(idx, pal)
        result = v2p.page_rgb_to_img(out_rgb)

        # live label values
        colors_v.config(text=str(ncolors))
        desp_v.config(text=str(despeckle))
        eps_v.config(text=f'{eps:.2f}')
        area_v.config(text=str(min_area))
        med_v.config(text=str(median))
        npts = sum(len(pts) for _c, pts in polys)
        info.config(text=f'frame {i}/{nframes-1}   t={i/args.fps:5.1f}s\n'
                         f'polys = {len(polys)}\n'
                         f'verts = {npts}\n'
                         f'colours used = {len(set(c for c,_ in polys))}\n'
                         f'(intro: ~3 typ / 56 max)')
        return orig, quant, result

    def show():
        state['idx'] = int(round(frame_scale.get()))
        fr_label.config(text=str(state['idx']))
        orig, quant, result = compute()
        sc = state['scale']
        pad = 6 * sc
        panels = [orig, quant, result]
        big = [p.resize((W * sc, H * sc), Image.NEAREST) for p in panels]
        cw = big[0].width * 3 + pad * 2
        combo = Image.new('RGB', (cw, big[0].height), (40, 40, 40))
        for k, b in enumerate(big):
            combo.paste(b, (k * (big[0].width + pad), 0))
        photo['img'] = ppm_photo(combo)
        canvas.config(image=photo['img'])

    # bind: re-render on release of any slider, and on frame drag
    frame_scale.bind('<ButtonRelease-1>', lambda e: show())
    for s in (colors_s, eps_s, area_s, med_s):
        s.bind('<ButtonRelease-1>', lambda e: show())

    # play loop
    def play_tick():
        if state['playing']:
            nxt = state['idx'] + 1
            if nxt >= nframes:
                nxt = 0
            state['idx'] = nxt
            frame_scale.set(nxt)
            show()
            root.after(120, play_tick)

    def toggle_play():
        state['playing'] = not state['playing']
        play_btn.config(text='Stop' if state['playing'] else 'Play')
        if state['playing']:
            play_tick()
    play_btn.config(command=toggle_play)

    def set_scale(d):
        state['scale'] = max(1, min(4, state['scale'] + d))
        show()
    zf = tk.Frame(left); zf.pack(pady=(12, 0))
    tk.Label(zf, text='Zoom').pack(side='left')
    tk.Button(zf, text='-', width=3, command=lambda: set_scale(-1)).pack(side='left')
    tk.Button(zf, text='+', width=3, command=lambda: set_scale(1)).pack(side='left')

    show()
    root.mainloop()
    rd.close()


if __name__ == '__main__':
    main()
