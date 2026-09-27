#!/usr/bin/env python3
"""build_check -- the end of every ATR build (build.ps1 transcribes the whole run into
out/build_check_<N>.txt): what the game's code occupies and what is still free, per
RAM region and in zero page, then the skill-pass gates and the cycle table against
the previous build.

  1. RAM map of awgame.xex: every loaded segment inside its region, used / free /
     ceiling, collisions
  2. zero page: which $80-$FF bytes the assembled game touches, the free ones
  3. skill_tips   -- rule violations left (counts)
  4. skill_check  -- the rewrites against the original behaviour (spec)
  5. skill_verify (+ _intro) -- old build vs new build, bit-identical + cycles
  6. skill_measure -- hot-path cycles, this build vs the previous one (out/measure_last.txt)

    python tools/build_check.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _lst6502 as LS                                             # noqa: E402

# region: (lo, ceiling, what)  -- the ceilings are the ert guards in the sources
REGIONS = [
    (0x0900, 0x0BC0, 'game_text (op_drawstring, LOADING)'),
    (0x0BC0, 0x0DB0, 'game_sound covox init'),
    (0x0DB0, 0x0F80, 'game_diskio high-speed SIO (not called)'),
    (0x0F80, 0x1000, 'aw_raster adv_edges1'),
    (0x1000, 0x1DC0, 'game_text_data (font + strings)'),
    (0x1DC0, 0x1F00, 'game_cellcache cold block'),
    (0x1F00, 0x1F80, 'game_vm_optab dispatch tables'),
    (0x2000, 0x4000, 'main code chain (under the MEMAC-B window)'),
    (0x9000, 0x9600, 'pal_data'),
    (0x9A00, 0x9C00, 'row_lo / row_hi (page-aligned span LUT)'),
    (0x9EA8, 0xA000, 'game_unpack ZX02 decoder (part load)'),
    (0xA000, 0xAA00, 'fmul squares + poly LUTs'),
    (0xAA00, 0xB000, 'game_cellcache hot block'),
    (0xB400, 0xC000, 'game_diskio + game_sound (under the OS ROM)'),
]


def segments(xex):
    d = open(xex, 'rb').read()
    p, segs = 0, []
    while p + 4 <= len(d):
        lo = d[p] | (d[p + 1] << 8)
        if lo == 0xFFFF:
            p += 2
            continue
        hi = d[p + 2] | (d[p + 3] << 8)
        p += 4 + hi - lo + 1
        if lo >= 0x02E0 and hi <= 0x02E3:
            continue                                   # RUN / INI vectors
        segs.append((lo, hi))
    return segs


def ram_map():
    segs = segments(os.path.join(PROJ, 'awgame.xex'))
    print('RAM map (awgame.xex)')
    print()
    print('  %-13s %6s %6s %6s  %s' % ('region', 'used', 'free', 'size', 'what'))
    tot_free = 0
    bad = []
    for lo, ceil, what in REGIONS:
        inside = [(a, b) for a, b in segs if lo <= a < ceil]
        used = sum(b - a + 1 for a, b in inside)
        end = max((b for a, b in inside), default=lo - 1)
        free = ceil - 1 - end
        tot_free += max(0, free)
        if end >= ceil:
            bad.append((lo, ceil, end))
        print('  $%04X-$%04X %6d %6d %6d  %s' % (lo, ceil - 1, used, free, ceil - lo, what))
    outside = [(a, b) for a, b in segs if not any(lo <= a < ceil for lo, ceil, _ in REGIONS)]
    for a, b in outside:
        print('  $%04X-$%04X %6d  (segment outside every known region)' % (a, b, b - a + 1))
    print()
    print('  %d B of code RAM free in the regions above' % tot_free)
    for lo, ceil, end in bad:
        print('  OVERFLOW: $%04X-$%04X ends at $%04X' % (lo, ceil - 1, end))
    s = sorted(segs)
    for (a, b), (c, d) in zip(s, s[1:]):
        if c <= b:
            print('  COLLISION: $%04X-$%04X and $%04X-$%04X' % (a, b, c, d))
    print()
    return not bad


def zp_map():
    ins = LS.read('game')
    used = set()
    for i in ins:
        if i.data or i.mn is None:
            continue
        if i.mode in ('zp', 'zpx', 'zpy', 'izx', 'izy'):
            used.add(i.arg)
            if i.mode in ('izx', 'izy'):
                used.add((i.arg + 1) & 0xFF)
    free = [a for a in range(0x80, 0x100) if a not in used]
    print('zero page $80-$FF: %d used, %d free' % (128 - len(free), len(free)))
    print('  free: ' + ' '.join('%02X' % a for a in free))
    print()


def run(title, args):
    print('=' * 78)
    print(title)
    print('=' * 78)
    sys.stdout.flush()
    p = subprocess.run([sys.executable] + args, cwd=PROJ, capture_output=True, text=True)
    print((p.stdout + p.stderr).rstrip())
    print()
    return p.returncode == 0


def main():
    ok = ram_map()
    zp_map()
    run('skill_tips (rule violations left in the game + intro; list: out/skill_tips.txt)',
        ['tools/skill_tips.py', '--summary'])
    ok = run('skill_check (rewrites vs the original behaviour)', ['tools/skill_check.py', '300']) and ok
    ok = run('skill_verify (old build vs new build)', ['tools/skill_verify.py', '200']) and ok
    ok = run('skill_verify_intro (old intro vs new intro)',
             ['tools/skill_verify_intro.py', '150']) and ok
    if os.path.exists(os.path.join(PROJ, 'out', 'bench', 'ref', 'awgame.xex')):
        ok = run('_verify_sndirq (game sound player: reference build vs this build)',
                 ['tools/_verify_sndirq.py']) and ok
    last = os.path.join(PROJ, 'out', 'measure_last.txt')
    if os.path.exists(last):
        run('skill_measure (hot-path cycles: previous build -> this build)',
            ['tools/skill_measure.py', '--cmp', 'last', '--save', 'last'])
    else:
        run('skill_measure (hot-path cycles)', ['tools/skill_measure.py', '--save', 'last'])
    before = os.path.join(PROJ, 'out', 'measure_before.txt')
    if os.path.exists(before):
        run('skill_measure (the pass so far: before the 2026-09-22 pass -> this build)',
            ['tools/skill_measure.py', '--cmp', 'before'])
    print('build_check: %s' % ('all gates hold' if ok else 'GATE FAILED'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
