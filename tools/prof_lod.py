#!/usr/bin/env python3
"""
prof_lod.py - GAME: would a woll3d-style DETAIL/quality knob speed the game up, and at what cost?

woll3d is render-bound: lowering DETAIL (40 vs 80 rays, 4 vs 2-px slices, sprite cap) directly
raises FPS. The AW game is VBLANK-PACED: the VM holds each frame VAR_PAUSE_SLICES (N) vblanks no
matter how fast the render is. So a quality knob only buys anything where the render OVERRUNS its
N-vblank budget (heavy scenes drop frames). This measures two things, no engine changes:

  (A) OVERRUN  -- per part: render time (decode+raster+page copy, in vblanks) vs the hold N.
                  overrun>1 means the scene already runs slower than intended -> a knob helps.
  (B) LOD COST -- a "cull small polygons" knob (woll3d's sprite-cap analog): for area thresholds,
                  how much CPU it saves vs how many drawn pixels (detail) it drops.

  python tools/prof_lod.py            # all parts, 250 frames
  python tools/prof_lod.py 16005 400  # arene
"""
import os, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_atari, sim_atari

PARTS = [(16002, "water"), (16003, "jail"), (16004, "cite"),
         (16005, "arene"), (16006, "luxe"), (16007, "final")]
PAL_BUDGET = 35568
BYTES_PER_CYC = 14.18 / 1.773447
CPU_DECODE_PER_VERT = 60
CPU_RASTER_PER_SPAN = 142
LR_PAGE = 160 * 200
THRESH = [16, 64, 256, 1024]            # cull polys with 320-space bbox area below T (px^2)

# Rapidus: CPU work runs at 11x (fast SRAM), but VBXE accesses (BCB writes, poly
# fetch reads) stay native ~1.77 MHz (see rapidus-timing memory). Effective cost in
# native-cycle-equivalents = vbxe_part + cpu_part/11. Splits per the perf model:
#   span 142 = ~60 VBXE (BCB DST+WIDTH+mode+fire) + ~82 CPU math
#   vert  60 = ~15 VBXE (poly-byte reads) + ~45 CPU math
RAP_MULT = 11.0
RAP_RASTER_PER_SPAN = 60 + 82 / RAP_MULT      # ~67
RAP_DECODE_PER_VERT = 15 + 45 / RAP_MULT      # ~19
RAP_PAGECOPY = 4000                           # blitter-bound (8x native), ~unchanged by Rapidus


def profile_part(part, frames):
    polys = []                          # (area, verts, spans, pixels) per leaf fill
    holds = []
    cur = {"area": 0, "verts": 0, "spans": 0, "px": 0}
    _of, _ofs = sim_atari.Sim.fill, game_atari.GameAtari.fill_span

    def fill(self, off, color, zoom, ptx, pty):
        bbw = self.mul(self.by(off), zoom)
        bbh = self.mul(self.by(off + 1), zoom)
        cur.update(area=bbw * bbh, verts=self.by(off + 2), spans=0, px=0)
        r = _of(self, off, color, zoom, ptx, pty)
        polys.append((cur["area"], cur["verts"], cur["spans"], cur["px"]))
        return r

    def fill_span(self, sx, sy, ln, color):
        cur["spans"] += 1; cur["px"] += max(0, ln)
        return _ofs(self, sx, sy, ln, color)

    OPS = game_atari.GameAtari.OPS
    i_ud = [k for k in range(len(OPS)) if OPS[k].__name__ == 'op_updatedisplay'][0]
    o_ud = OPS[i_ud]

    def op_ud(self):
        r = o_ud(self); holds.append(self.frames[-1][2]); return r

    game_atari.GameAtari.fill = fill
    game_atari.GameAtari.fill_span = fill_span
    OPS[i_ud] = op_ud
    try:
        vm = game_atari.GameAtari(part); vm.run(frames)
    finally:
        game_atari.GameAtari.fill = _of
        game_atari.GameAtari.fill_span = _ofs
        OPS[i_ud] = o_ud

    nf = max(len(holds), 1)
    # (A) render cost / frame  (decode + raster + ~1 page copy of 8000 cyc)
    cpu = sum(v * CPU_DECODE_PER_VERT + s * CPU_RASTER_PER_SPAN
              for (_a, v, s, _p) in polys) / nf + 8000
    avgN = sum(max(1, h) for h in holds) / nf
    render_vbl = cpu / PAL_BUDGET
    overrun = render_vbl / max(avgN, 1)
    # Rapidus-effective render (CPU work /11, VBXE accesses native -- see RAP_* above)
    cpu_rap = sum(v * RAP_DECODE_PER_VERT + s * RAP_RASTER_PER_SPAN
                  for (_a, v, s, _p) in polys) / nf + RAP_PAGECOPY
    render_vbl_rap = cpu_rap / PAL_BUDGET
    overrun_rap = render_vbl_rap / max(avgN, 1)
    # "chunky" (render every other scanline, HEIGHT=1 spans) ~= halves the SPAN cost
    cpu_rap_chunky = sum(v * RAP_DECODE_PER_VERT + (s // 2) * RAP_RASTER_PER_SPAN
                         for (_a, v, s, _p) in polys) / nf + RAP_PAGECOPY
    overrun_rap_chunky = (cpu_rap_chunky / PAL_BUDGET) / max(avgN, 1)

    # (B) LOD: cumulative cost+pixels in polys BELOW each area threshold
    tot_cpu = sum(v * CPU_DECODE_PER_VERT + s * CPU_RASTER_PER_SPAN for (_a, v, s, _p) in polys) or 1
    tot_px = sum(p for (_a, _v, _s, p) in polys) or 1
    lod = []
    for T in THRESH:
        c = sum(v * CPU_DECODE_PER_VERT + s * CPU_RASTER_PER_SPAN
                for (a, v, s, _p) in polys if a < T)
        px = sum(p for (a, _v, _s, p) in polys if a < T)
        n = sum(1 for (a, _v, _s, _p) in polys if a < T)
        lod.append((T, 100 * n / max(len(polys), 1), 100 * c / tot_cpu, 100 * px / tot_px))
    return dict(frames=nf, avgN=avgN, render_vbl=render_vbl, overrun=overrun,
                render_vbl_rap=render_vbl_rap, overrun_rap=overrun_rap,
                overrun_rap_chunky=overrun_rap_chunky, lod=lod)


def main():
    if len(sys.argv) > 1:
        part = int(sys.argv[1]); frames = int(sys.argv[2]) if len(sys.argv) > 2 else 400
        parts = [(part, dict(PARTS).get(part, f"p{part}"))]
    else:
        parts = PARTS; frames = 250

    print("== woll3d-style DETAIL knob for the AW game: would it help? (prof_lod) ==\n")
    print("(A) OVERRUN -- does the render exceed the vblank-paced budget? (>1 = drops frames)\n")
    print(f"  {'part':7}{'holdN':>6}{'stock':>8}{'RAPIDUS':>9}{'RAP+chunky':>12}  verdict (Rapidus)")
    for p, name in parts:
        r = profile_part(p, frames)
        rap = r["overrun_rap"]
        if rap > 1.05:
            v = "OVERRUNS -> chunky/LOD helps"
        elif r["overrun_rap_chunky"] < rap < 1.05:
            v = "fits (chunky only adds detail loss, no speed)"
        else:
            v = "fits budget -> paced (knob does nothing)"
        print(f"  {name:7}{r['avgN']:6.1f}{r['overrun']:7.2f}x{rap:8.2f}x{r['overrun_rap_chunky']:11.2f}x  {v}")
        r["_name"] = name

    print("\n(B) LOD COST -- 'cull polygons with 320-space bbox area < T':")
    print("    for each T: % of polys culled / % of render CPU saved / % of drawn pixels lost\n")
    print(f"  {'part':7}" + "".join(f"{('<'+str(T)):>18}" for T in THRESH))
    for p, name in parts:
        r = profile_part(p, frames)
        cells = "".join(f"  {n:4.0f}%/{c:3.0f}%/{px:3.0f}%" for (T, n, c, px) in r["lod"])
        print(f"  {name:7}{cells}")

    print()
    print("Read (B) as cull%/save%/loss%. A good knob saves a lot of CPU for little pixel loss.")
    print()
    print("CONCLUSION: on RAPIDUS every scene fits the vblank budget (overrun_rap < 1, arene 0.68x).")
    print("The game is vblank-PACED, so a faster render (LOD, chunky, BCB-chaining) buys NOTHING ->")
    print("it would only lose detail. The framerate IS the pace (holdN vblanks = ~6-12 fps) = faithful")
    print("AW. If it feels too choppy the lever is the PACE (VAR_PAUSE_SLICES / the un-wired PAL/NTSC")
    print("timing), NOT the renderer. (On a STOCK 6502 only arene overruns -> there a knob would help.)")


if __name__ == "__main__":
    main()
