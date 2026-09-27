#!/usr/bin/env python3
"""skill_verify -- bit-identity of the skill passes, old build vs new build.

The OLD build is generated, not kept: every `.if 1` of src/ + src_game/ flipped to
`.if 0` (= the code before the skill passes, which is what those blocks keep in their
.else) and the GAME_ZP switch dropped, assembled into out/skill_orig/, used, deleted.
The NEW build is the root awgame.xex + out/awgame.lst. Both run on the cycle-exact
6502 (tools/_cpu6502.py) through tools/_verify_skillpass.py's tests:

  A) poly_draw on random shape trees (fill + hier, zoom 64 / != 64, on/off screen,
     half + full detail), LR and SR-320: identical span stream (the span BCB at every
     START) + identical decoder state
  B) the VM opcodes on random operands: identical variables / threads / VM globals
  C) emit_run (text): identical spans
and prints the cycles old -> new per group. Seconds.

    python tools/skill_verify.py [N]        (N shape trees per mode, default 300)
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

TMP = os.path.join(PROJ, 'out', 'skill_orig')
BINS = ('fmul.bin', 'water_pal.bin', 'polylut.bin', 'recip.bin')
IF1 = re.compile(r'^(\s*)\.if\s+1\b', re.I)


def build_orig():
    if os.path.exists(TMP):
        shutil.rmtree(TMP)
    for d in ('src', 'src_game', 'out'):
        os.makedirs(os.path.join(TMP, d))
    for d in ('src', 'src_game'):
        for fn in os.listdir(os.path.join(PROJ, d)):
            if not fn.endswith(('.asm', '.inc')):
                continue
            raw = open(os.path.join(PROJ, d, fn), 'rb').read().decode('latin-1')
            rows = raw.split('\n')
            for k, r in enumerate(rows):
                if IF1.match(r):
                    rows[k] = IF1.sub(r'\1.if 0', r, count=1)
                elif r.startswith('GAME_ZP = 1'):
                    rows[k] = ';' + r
            open(os.path.join(TMP, d, fn), 'wb').write('\n'.join(rows).encode('latin-1'))
    for b in BINS:
        shutil.copy(os.path.join(PROJ, 'out', b), os.path.join(TMP, 'out', b))
    p = subprocess.run([os.path.join(PROJ, 'mads.exe'), 'src_game/awgame.asm', '-o:awgame.xex',
                        '-l:awgame.lst'], cwd=TMP, capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(os.path.join(TMP, 'awgame.xex')):
        print(p.stdout[-2000:], p.stderr[-2000:])
        raise SystemExit('the OLD build did not assemble')
    return os.path.join(TMP, 'awgame.xex'), os.path.join(TMP, 'awgame.lst')


# the cells the rewrites moved between RAM and zero page (GAME_ZP): scratch of the
# decoder / raster / VM opcodes -- masked from the VM state comparison like the
# harness already masks the other opcode-local scratch
MOVED = ('numv', 'i_idx', 'j_idx', 'hgt_lo', 'hgt_hi', 'nverts', 'fill_col', 'poly_color',
         'bbw', 'bbh', 'bx', 'by', 'word_lo', 'word_hi', 'ccol', 'hcount', 't_cbx', 't_rbits',
         't_inrun', 't_i0', 't_j', 'zp_dlo', 'zp_dmid', 'acc_lo', 'acc_hi', 't_lo', 't_hi', 'zp_n',
         'blit_pg', 'hold', 'pend_pal', 'qp_lo', 'qp_hi', 'pl_lo', 'pl_mid')


def run_vmop(b, op, stream, seed, a=0):
    # the harness runs the draw opcodes with hires = 1 (no cell cache): the game only
    # ever has hires = 1 after set_render_mode(1), so put the machine in that mode
    fresh = b.fresh
    last = {}

    def fresh_sr():
        cpu, mem, log = fresh()
        last['mem'] = mem
        b.call(cpu, b.L['upload_bcb'])
        if 'set_render_mode' in b.L:
            cpu.a = 1
            b.call(cpu, b.L['set_render_mode'])
        log.clear()
        return cpu, mem, log
    b.fresh = fresh_sr
    try:
        st, cyc = V.run_vmop(b, op, stream, seed, a)
    finally:
        b.fresh = fresh
    zp, ramb, vm, ok, log = st
    zp = bytearray(zp)
    ramb = bytearray(ramb)
    L = b.L
    for k in MOVED:
        if k not in L:
            continue
        for d in (0, 1):
            a_ = L[k] + d
            if 0x80 <= a_ < 0x100:
                zp[a_ - 0x80] = 0
            if 0x9C00 <= a_ < 0x9C80:
                ramb[a_ - 0x9C00] = 0
    for a_ in range(0xD4, 0x100):              # the GAME_ZP block (old build: unused)
        zp[a_ - 0x80] = 0
    # span parameters (consumed by the span: the fused spans write the BCB straight)
    # and the mode cache tag (checked for consistency instead, see cache_ok)
    for k in ('sx_lo', 'sx_hi', 'sy', 'slen_lo', 'slen_hi', 'a_lo', 'a_hi', 'b_lo', 'b_hi',
              'last_scol'):
        if k in L and 0x80 <= L[k] < 0x100:
            zp[L[k] - 0x80] = 0
    for a_ in (0x92, 0x93, 0xA0, 0xA1, 0xA6, 0xA7, 0xA9, 0xAA, 0xB2, 0xBB):
        zp[a_ - 0x80] = 0
    for a_ in range(0x9C1D, 0x9C22):           # i_idx .. hgt_hi in the old build
        ramb[a_ - 0x9C00] = 0
    for a_ in range(0x9C30, 0x9C80):           # poly_color .. the text / VM scratch
        ramb[a_ - 0x9C00] = 0
    # the span stream: compare the blit the hardware sees (fields the mode uses)
    return (bytes(zp), bytes(ramb), vm, ok, [norm_bcb(x) for x in log],
            cache_ok(last['mem'], L)), cyc


BCB = 0x8100                     # the span BCB in the CPU's MEMAC-A view


def cache_ok(mem, L):
    """last_scol is a cache tag of the span BCB's mode fields: $FF (unknown -> the next
    span writes them) or exactly span_mode(last_scol). The builds may leave a different
    tag (the new one writes a shape's mode up front, before its first span) -- what must
    hold in both is that the tag never lies; the spans themselves are compared exactly."""
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


def norm_bcb(b):
    """a span BCB as the blitter uses it: in the fill modes (AND = 0) the source
    fields are never read."""
    b = bytearray(b)
    if b[15] == 0:
        for i in (0, 1, 2, 3, 4, 5):
            b[i] = 0
    return bytes(b)


def run_shape(b, stream, seed, sr):
    cpu, mem, log = b.fresh()
    L = b.L
    b.call(cpu, L['upload_bcb'])
    cpu.a = 1 if sr else 0
    if 'set_render_mode' in L:
        b.call(cpu, L['set_render_mode'])
    log.clear()
    V.SR_MODE = sr
    lg, stt, cyc = _run_shape_on(b, cpu, mem, log, stream, seed, sr)
    return [norm_bcb(x) for x in lg], stt, cyc


def _run_shape_on(b, cpu, mem, log, stream, seed, sr):
    import random
    rng = random.Random(seed)
    L = b.L
    mem[0x4000:0x4000 + len(stream)] = stream
    zoom = rng.choice([64, 64, 64, 32, 48, 80, 128, 200, 300, 1000])
    for k, v in (('hires', 1 if sr else 0), ('poly_base_adj', 0), ('cc_baking', 0), ('cc_flag', 0)):
        if k in L:
            SV.poke(mem, L, k, v)
    h = rng.randrange(2)
    SV.poke(mem, L, 'poly_bcb_h', 0 if sr else h)
    V.w16(mem, L['dr_zoom'], zoom)
    V.w16(mem, L['dr_x'], rng.randrange(-40, 360) & 0xFFFF)
    V.w16(mem, L['dr_y'], rng.randrange(-40, 240) & 0xFFFF)
    mem[L['dr_col']] = rng.randrange(256)
    V.w16(mem, L['dr_off'], 0)
    mem[L['psp']] = 0
    mem[L['last_scol']] = 0xFF
    mem[L['memb_cur']] = 0
    if zoom == 64:
        V.w16(mem, L['rs_smc'] + 1, L['rs_fast'])
    else:
        V.w16(mem, L['rs_smc'] + 1, L['rs_z4'])
        b.call(cpu, L['rs_z4_set'])
    b.call(cpu, L['set_poly_ptr'])
    cpu.cyc = 0
    b.call(cpu, L['poly_draw'])
    # (sx / sy / slen: span parameters, consumed by the span itself -- the fused spans
    #  write the BCB straight; the span stream above compares what they produced)
    keys = ['dr_x', 'dr_y', 'dr_col', 'dr_off', 'pb_ptr', 'poly_bnk', 'memb_cur', 'psp', 'scol',
            'rpar']
    state = tuple((k, mem[L[k]], mem[L[k] + 1] if k in ('dr_x', 'dr_y', 'dr_off', 'pb_ptr') else 0)
                  for k in keys if k in L)
    state += tuple(bytes(mem[L[k]:L[k] + 64]) for k in
                   ('pts_xlo', 'pts_xhi', 'pts_ylo', 'pts_yhi')) + (cache_ok(mem, L),)
    return log, state, cpu.cyc


def run_thread_entry(b, seed):
    """vm_run_thread up to the first opcode fetch: thread X's PC -> window + bank."""
    import random
    rng = random.Random(seed)
    cpu, mem, log = b.fresh()
    L = b.L
    for i in range(64):
        mem[L['tpc_lo'] + i] = rng.randrange(256)
        mem[L['tpc_hi'] + i] = rng.randrange(256)
    mem[L['vm_ssp']] = rng.randrange(64)
    cpu.x = rng.randrange(64)
    cpu.cyc = 0
    b.call(cpu, L['vm_run_thread'], stop=L['vm_fetch'])
    st = tuple(mem[L[k]] for k in ('pl_wlo', 'pl_whi', 'pl_bank', 'vm_t', 'vm_ssp'))
    return st + (cpu.pc == L['vm_fetch'],), cpu.cyc


def test_thread_entry(old, new):
    co = cn = fails = 0
    for seed in range(300):
        so, c1 = run_thread_entry(old, seed)
        sn, c2 = run_thread_entry(new, seed)
        co += c1
        cn += c2
        fails += so != sn
    print('   %-14s %4d runs, mismatches=%d, cycles old=%8d new=%8d (%+.1f%%)'
          % ('vm_run_thread', 300, fails, co, cn, 100.0 * (cn - co) / max(1, co)))
    return fails == 0


def text_setup(b, cpu, mem):
    """what do_drawstring does before its runs: this colour's BCB mode fields, and the
    fused span the runs take (the new build; the old one checks the colour per run)."""
    L = b.L
    if 'span_mode' not in L:
        return
    cpu.a = mem[L['scol']]
    b.call(cpu, L['span_mode'])
    mem[L['draw_scanline_fast.dsf_m'] if 'draw_scanline_fast.dsf_m' in L
        else L['dsf_m']] = 0xD0


def run_glyph(b, seed):
    """draw_glyph: one random character at a random column / row (rows near the bottom
    exercise the py >= 200 skip), LR; the span stream it produces."""
    import random
    rng = random.Random(9000 + seed)
    cpu, mem, log = b.fresh()
    L = b.L
    b.call(cpu, L['upload_bcb'])
    if 'set_render_mode' in L:
        cpu.a = 0
        b.call(cpu, L['set_render_mode'])
    log.clear()
    SV.poke(mem, L, 'hires', 0)
    mem[L['t_ch']] = rng.randrange(0x20, 0x80)
    V.w16(mem, L['t_cbx'], rng.randrange(0, 312))
    mem[L['txt_y']] = rng.choice([rng.randrange(0, 192), rng.randrange(192, 200)])
    mem[L['scol']] = rng.randrange(16)
    mem[L['last_scol']] = 0xFF
    text_setup(b, cpu, mem)
    cpu.cyc = 0
    b.call(cpu, L['draw_glyph'])
    return [norm_bcb(x) for x in log], cpu.cyc


def test_glyph(old, new):
    co = cn = fails = 0
    for seed in range(300):
        so, c1 = run_glyph(old, seed)
        sn, c2 = run_glyph(new, seed)
        co += c1
        cn += c2
        fails += so != sn
    print('C) draw_glyph: 300 runs, mismatches=%d, cycles old=%d new=%d (%+.1f%%)'
          % (fails, co, cn, 100.0 * (cn - co) / max(1, co)))
    return fails == 0


def run_cold(b, seed, which):
    """the cold thread-table loops: vm_reset_threads, and snapshot_state ->
    restore_state as a round trip. Compares the whole thread table + the snapshot."""
    import random
    rng = random.Random(4000 + seed)
    cpu, mem, log = b.fresh()
    L = b.L
    for t in ('tpc_lo', 'tpc_hi', 'tpause', 'treq_lo', 'treq_hi', 'tpreq'):
        for i in range(64):
            mem[L[t] + i] = rng.randrange(256)
    for k in ('vm_cur1', 'vm_cur2', 'vm_cur3', 'vm_maxt', 'req_any'):
        if k in L:
            SV.poke(mem, L, k, rng.randrange(64))    # (+ its SMC operand copies)
    cpu.cyc = 0
    if which == 'xfer':                         # pages_xfer: every BCB at every START
        for i in range(21):
            mem[0x8100 + i] = rng.randrange(256)
        mem[L['vm_s1']] = rng.randrange(2)          # 0 = save, else restore
        b.call(cpu, L['pages_xfer'])
        # the four page copies are independent (distinct pages / slots): the SET of
        # BCBs at the STARTs is the result (the order was already reversed 0..3 -> 3..0
        # by the 2026-09-09 pass)
        return (tuple(sorted(log)), mem[L['last_scol']], mem[L['bcb_pg']]), cpu.cyc
    if which == 'reset':
        b.call(cpu, L['vm_reset_threads'])
    else:
        b.call(cpu, L['snapshot_state'])
        for t in ('tpc_lo', 'tpc_hi', 'tpause', 'treq_lo', 'treq_hi'):
            for i in range(64):
                mem[L[t] + i] = 0
        b.call(cpu, L['restore_state'])
    st = tuple(bytes(mem[L[t]:L[t] + 64]) for t in
               ('tpc_lo', 'tpc_hi', 'tpause', 'treq_lo', 'treq_hi', 'tpreq'))
    st += (bytes(mem[0x9600:0x9600 + 336]),)          # SNAP + SNAP_G
    st += tuple(mem[L[k]] for k in ('vm_maxt', 'req_any') if k in L)
    return st, cpu.cyc


def test_cold(old, new):
    ok = True
    for which in ('reset', 'snapshot', 'xfer'):
        co = cn = fails = 0
        for seed in range(50):
            so, c1 = run_cold(old, seed, which)
            sn, c2 = run_cold(new, seed, which)
            co += c1
            cn += c2
            fails += so != sn
        print('   %-14s %4d runs, mismatches=%d, cycles old=%8d new=%8d (%+.1f%%)'
              % (which, 50, fails, co, cn, 100.0 * (cn - co) / max(1, co)))
        ok = ok and fails == 0
    return ok


def run_bake_span(b, seed):
    """bake_span with the state cc_bake leaves it in: HEIGHT written once by cc_bake,
    the colour mode written by fill_poly_int before the shape's first span. Compares
    the BCB the blitter sees at the START plus the extents it tracks."""
    import random
    rng = random.Random(3000 + seed)
    cpu, mem, log = b.fresh()
    L = b.L
    b.call(cpu, L['upload_bcb'])
    log.clear()
    h = rng.randrange(2)
    SV.poke(mem, L, 'poly_bcb_h', h)
    mem[BCB + 14] = h                        # cc_bake: HEIGHT once per bake
    mem[BCB + 13] = 0                        # the shape's mode (solid): WIDTH+1 0,
    mem[BCB + 15] = 0                        #   AND 0, CTRL BLT_COPY
    mem[BCB + 20] = 0
    mem[L['scol']] = rng.randrange(16)
    mem[L['sx_lo']] = rng.randrange(160)
    mem[L['sx_hi']] = 0x40
    mem[L['sy']] = rng.randrange(200)
    mem[L['slen_lo']] = rng.randrange(60)
    mem[L['cc_flag']] = 0
    for k in ('cc_x0', 'cc_y0', 'cc_x1', 'cc_y1'):
        mem[L[k]] = rng.randrange(256)
    cpu.cyc = 0
    b.call(cpu, L['bake_span'])
    st = ([norm_bcb(x) for x in log],
          tuple(mem[L[k]] for k in ('cc_x0', 'cc_y0', 'cc_x1', 'cc_y1', 'cc_flag')))
    # (last_scol: the new build clears the tag here -- the old one left it lying, which
    #  cc_bake papered over afterwards; the BCB stream above is what the blitter sees)
    return st, cpu.cyc


def test_bake_span(old, new):
    co = cn = fails = 0
    for seed in range(300):
        so, c1 = run_bake_span(old, seed)
        sn, c2 = run_bake_span(new, seed)
        co += c1
        cn += c2
        fails += so != sn
    print('   %-14s %4d runs, mismatches=%d, cycles old=%8d new=%8d (%+.1f%%)'
          % ('bake_span', 300, fails, co, cn, 100.0 * (cn - co) / max(1, co)))
    return fails == 0


def main():
    import random
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    try:
        oxex, olst = build_orig()
        old = V.Build(oxex, olst)
        new = V.Build(os.path.join(PROJ, 'awgame.xex'), os.path.join(PROJ, 'out', 'awgame.lst'))
    finally:
        pass
    ok = True
    for sr in (False, True):
        co = cn = spans = fails = 0
        for seed in range(n):
            rng = random.Random(1000 + seed)
            stream = V.gen_tree(rng, rng.random() < 0.3)
            lo, so, c1 = run_shape(old, stream, seed, sr)
            ln, sn, c2 = run_shape(new, stream, seed, sr)
            co += c1
            cn += c2
            spans += len(lo)
            if lo != ln or so != sn:
                fails += 1
                if fails <= 3:
                    print('  MISMATCH seed', seed, 'sr', sr, 'spans', len(lo), len(ln), 'state', so == sn)
                    for i, (x, y) in enumerate(zip(lo, ln)):
                        if x != y:
                            print('   span', i, x.hex(), y.hex())
                            break
                    if so != sn:
                        for x, y in zip(so, sn):
                            if x != y:
                                print('   state', x if not isinstance(x, bytes) else '(pts)', y if not isinstance(y, bytes) else '')
        print('A) poly_draw %s: %d trees, %d spans, mismatches=%d, cycles old=%d new=%d (%.1f%% saved)'
              % ('SR-320' if sr else 'LR', n, spans, fails, co, cn, 100.0 * (co - cn) / co))
        ok = ok and fails == 0
    V.run_vmop_orig = V.run_vmop
    print('B) VM opcodes:')
    extra = [
        ('op_setpal', 200, lambda r: (bytes([r.randrange(256) for _ in range(6)]), 0)),
        ('op_music', 200, lambda r: (bytes([r.randrange(256) for _ in range(8)]), 0)),
        ('op_call', 200, lambda r: (bytes([r.randrange(0x30), r.randrange(256)] + [0] * 4), 0)),
        ('op_ret', 200, lambda r: (bytes([0] * 4), 0)),
        ('op_jmp', 200, lambda r: (bytes([r.randrange(0x30), r.randrange(256)] + [0] * 4), 0)),
        ('op_sound', 200, lambda r: (bytes([0, r.randrange(256), r.randrange(64), r.randrange(256),
                                            r.randrange(256)] + [0] * 4), 0)),
    ]
    for op, cnt, gen in V.VM_OPS + extra:
        co = cn = fails = 0
        for seed in range(cnt):
            rng = random.Random(5000 + seed)
            stream, a = gen(rng)
            so, c1 = run_vmop(old, op, stream, seed, a)
            sn, c2 = run_vmop(new, op, stream, seed, a)
            co += c1
            cn += c2
            if so != sn:
                fails += 1
                if fails <= 2:
                    for i, (x, y) in enumerate(zip(so, sn)):
                        if x != y:
                            if isinstance(x, (bytes, bytearray)):
                                d = [j for j in range(min(len(x), len(y))) if x[j] != y[j]][:6]
                                print('   %s seed %d: part %d differs at %s' % (op, seed, i, d))
                            else:
                                print('   %s seed %d: part %d differs' % (op, seed, i))
        print('   %-14s %4d runs, mismatches=%d, cycles old=%8d new=%8d (%+.1f%%)'
              % (op, cnt, fails, co, cn, 100.0 * (cn - co) / max(1, co)))
        ok = ok and fails == 0
    ok = test_thread_entry(old, new) and ok
    ok = test_cold(old, new) and ok
    ok = test_bake_span(old, new) and ok
    ok = test_glyph(old, new) and ok
    ok = V.test_text(old, new) and ok
    shutil.rmtree(TMP, ignore_errors=True)
    print('RESULT:', 'OK - bit-identical' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
