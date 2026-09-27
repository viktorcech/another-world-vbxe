#!/usr/bin/env python3
"""skill_verify_intro -- the same bit-identity check as skill_verify, for the INTRO.

The intro (src/awvbxe.asm) has its own copy of the polygon decoder + raster
(src/aw_polygon.asm, src/aw_raster.asm) plus the replayer, text and settings; only
src/aw_vbxe.asm is shared with the game. The OLD build is generated the same way --
every `.if 1` in src/ flipped to `.if 0`, assembled into out/skill_orig_intro/, used,
deleted -- and both builds run the same random shape trees through poly_draw on the
cycle-exact 6502: identical span stream (the span BCB at every START) + identical
decoder state, and the cycles of each.

    python tools/skill_verify_intro.py [N]        (N shape trees, default 300)
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _verify_skillpass as V                                     # noqa: E402
import _smcvars as SV                                             # noqa: E402

TMP = os.path.join(PROJ, 'out', 'skill_orig_intro')
BINS = ('fmul.bin', 'polylut.bin', 'recip.bin', 'intro_music.bin', 'intro_pal.bin',
        'intro_playlist.bin', 'intro_poly.bin', 'intro_sfx.bin', 'test_sfx.bin')
IF1 = re.compile(r'^(\s*)\.if\s+1\b', re.I)
BCB = 0x8100


def build_orig():
    if os.path.exists(TMP):
        shutil.rmtree(TMP)
    for d in ('src', 'out'):
        os.makedirs(os.path.join(TMP, d))
    for fn in os.listdir(os.path.join(PROJ, 'src')):
        if not fn.endswith(('.asm', '.inc')):
            continue
        raw = open(os.path.join(PROJ, 'src', fn), 'rb').read().decode('latin-1')
        rows = raw.split('\n')
        for k, r in enumerate(rows):
            if IF1.match(r):
                rows[k] = IF1.sub(r'\1.if 0', r, count=1)
        open(os.path.join(TMP, 'src', fn), 'wb').write('\n'.join(rows).encode('latin-1'))
    for b in BINS:
        src = os.path.join(PROJ, 'out', b)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(TMP, 'out', b))
    p = subprocess.run([os.path.join(PROJ, 'mads.exe'), 'src/awvbxe.asm', '-d:GAME_SEC=0',
                        '-o:awintro.xex', '-l:awintro.lst'], cwd=TMP, capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(os.path.join(TMP, 'awintro.xex')):
        print(p.stdout[-2000:], p.stderr[-2000:])
        raise SystemExit('the OLD intro did not assemble')
    return os.path.join(TMP, 'awintro.xex'), os.path.join(TMP, 'awintro.lst')


def norm_bcb(b):
    """a span BCB as the blitter uses it: in the fill modes (AND = 0) the source
    fields are never read."""
    b = bytearray(b)
    if b[15] == 0:
        for i in (0, 1, 2, 3, 4, 5):
            b[i] = 0
    return bytes(b)


def cache_ok(mem, L):
    """last_scol is a cache tag of the span BCB's mode fields: $FF or exactly what
    that colour's mode writes. The tag may differ between the builds (a shape's mode
    may go in up front), but it must never lie."""
    ls = mem[L['last_scol']]
    if ls == 0xFF:
        return True
    b = mem[BCB:BCB + 21]
    if ls < 0x10:
        return b[15] == 0 and b[16] == ls and b[20] == 0
    if ls == 0x10:
        return b[15] == 0 and b[16] == 8 and b[20] == 3
    if ls == 0x11:
        return b[15] == 0xFF and b[16] == 0 and b[20] == 0 and b[2] == 0 and b[5] == 1
    return False


def shape_zoom(seed):
    import random
    return random.Random(seed).choice([64, 64, 64, 32, 48, 80, 128, 200, 300, 1000])


def run_shape(b, stream, seed):
    import random
    rng = random.Random(seed)
    cpu, mem, log = b.fresh()
    L = b.L
    b.call(cpu, L['upload_bcb'])
    log.clear()
    mem[0x4000:0x4000 + len(stream)] = stream
    zoom = rng.choice([64, 64, 64, 32, 48, 80, 128, 200, 300, 1000])
    for k, v in (('poly_base_adj', 0), ('hires', 0)):
        if k in L:
            SV.poke(mem, L, k, v)
    if 'poly_bcb_h' in L:
        SV.poke(mem, L, 'poly_bcb_h', rng.randrange(2))
    V.w16(mem, L['dr_zoom'], zoom)
    V.w16(mem, L['dr_x'], rng.randrange(-40, 360) & 0xFFFF)
    V.w16(mem, L['dr_y'], rng.randrange(-40, 240) & 0xFFFF)
    mem[L['dr_col']] = rng.randrange(256)
    V.w16(mem, L['dr_off'], 0)
    mem[L['psp']] = 0
    mem[L['last_scol']] = 0xFF
    mem[L['memb_cur']] = 0
    if 'rs_smc' in L:
        if zoom == 64:
            V.w16(mem, L['rs_smc'] + 1, L['rs_fast'])
        elif 'rs_z4' in L:                       # the game's per-shape premultiply
            V.w16(mem, L['rs_smc'] + 1, L['rs_z4'])
            b.call(cpu, L['rs_z4_set'])
        else:
            V.w16(mem, L['rs_smc'] + 1, L['rs_slow'])
    b.call(cpu, L['set_poly_ptr'])
    cpu.cyc = 0
    b.call(cpu, L['poly_draw'])
    keys = ['dr_x', 'dr_y', 'dr_col', 'dr_off', 'pb_ptr', 'poly_bnk', 'memb_cur', 'psp', 'scol']
    state = tuple((k, mem[L[k]], mem[L[k] + 1] if k in ('dr_x', 'dr_y', 'dr_off', 'pb_ptr') else 0)
                  for k in keys if k in L)
    state += tuple(bytes(mem[L[k]:L[k] + 64]) for k in
                   ('pts_xlo', 'pts_xhi', 'pts_ylo', 'pts_yhi')) + (cache_ok(mem, L),)
    return [norm_bcb(x) for x in log], state, cpu.cyc


def run_plbyte(b, seed):
    """pl_byte: the playlist read (bank check, window pointer, the rare wrap)."""
    import random
    rng = random.Random(6000 + seed)
    cpu, mem, log = b.fresh()
    L = b.L
    win = rng.choice([0x4000, 0x5FFF, 0x40FF, 0x7FFF, 0x6ABC])
    V.w16(mem, L['pl_wlo'], win)
    mem[L['pl_bnk']] = 0x80 | rng.randrange(0x18, 0x1C)
    mem[L['memb_cur']] = rng.choice([mem[L['pl_bnk']], 0x98, 0])
    for i in range(0x4000, 0x8000, 0x111):
        mem[i] = rng.randrange(256)
    cpu.cyc = 0
    b.call(cpu, L['pl_byte'])
    st = (cpu.a, mem[L['pl_wlo']], mem[L['pl_wlo'] + 1], mem[L['pl_bnk']], mem[L['memb_cur']])
    return st, cpu.cyc


def test_plbyte(old, new):
    co = cn = fails = 0
    for seed in range(300):
        so, c1 = run_plbyte(old, seed)
        sn, c2 = run_plbyte(new, seed)
        co += c1
        cn += c2
        fails += so != sn
    print('INTRO pl_byte: 300 runs, mismatches=%d, cycles old=%d new=%d (%+.1f%%)'
          % (fails, co, cn, 100.0 * (cn - co) / max(1, co)))
    return fails == 0


def main():
    import random
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    oxex, olst = build_orig()
    old = V.Build(oxex, olst)
    new = V.Build(os.path.join(PROJ, 'awintro.xex'), os.path.join(PROJ, 'out', 'awintro.lst'))
    co = cn = spans = fails = 0
    z64 = [0, 0]                     # the intro draws ~97% of its shapes at zoom 64
    zot = [0, 0]
    for seed in range(n):
        rng = random.Random(1000 + seed)
        stream = V.gen_tree(rng, rng.random() < 0.3)
        lo, so, c1 = run_shape(old, stream, seed)
        ln, sn, c2 = run_shape(new, stream, seed)
        co += c1
        cn += c2
        b_ = z64 if shape_zoom(seed) == 64 else zot
        b_[0] += c1
        b_[1] += c2
        spans += len(lo)
        if lo != ln or so != sn:
            fails += 1
            if fails <= 3:
                print('  MISMATCH seed', seed, 'spans', len(lo), len(ln), 'state', so == sn)
                for i, (x, y) in enumerate(zip(lo, ln)):
                    if x != y:
                        print('   span', i, x.hex(), y.hex())
                        break
                for x, y in zip(so, sn):
                    if x != y:
                        print('   state', x if not isinstance(x, bytes) else '(pts)',
                              y if not isinstance(y, bytes) else '')
    print('INTRO poly_draw: %d trees, %d spans, mismatches=%d, cycles old=%d new=%d (%.1f%% saved)'
          % (n, spans, fails, co, cn, 100.0 * (co - cn) / max(1, co)))
    for nm, bk in (('zoom 64 (~97% of the intro)', z64), ('zoom != 64', zot)):
        if bk[0]:
            print('   %-28s old=%9d new=%9d (%.1f%% saved)'
                  % (nm, bk[0], bk[1], 100.0 * (bk[0] - bk[1]) / bk[0]))
    if not test_plbyte(old, new):
        fails += 1
    shutil.rmtree(TMP, ignore_errors=True)
    print('RESULT:', 'OK - bit-identical' if fails == 0 else 'FAILED')
    sys.exit(0 if fails == 0 else 1)


if __name__ == '__main__':
    main()
