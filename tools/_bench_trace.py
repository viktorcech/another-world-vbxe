#!/usr/bin/env python3
"""_bench_trace.py - single-step the _bench_frame machine and, when it halts (BRK,
illegal opcode, trap), print the last N instructions with their source lines.

    python tools/_bench_trace.py [N] [--max CYCLES]
"""
import os
import sys
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _bench_frame as B                                          # noqa: E402
import _sim6502 as S                                              # noqa: E402

PROJ = os.path.dirname(HERE)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    keep = int(args[0]) if args else 60
    mx = int(sys.argv[sys.argv.index('--max') + 1]) if '--max' in sys.argv else 1 << 40
    mc = B.Machine(os.path.join(PROJ, 'awgame.xex'), os.path.join(PROJ, 'awgame_full.atr'),
                   os.path.join(PROJ, 'out', 'awgame.lst'))
    src = {i.addr: (i.file, i.line, i.src) for i in mc.ins}
    hist = deque(maxlen=keep)
    n = 0
    try:
        while mc.cyc < mx:
            hist.append((mc.pc, mc.a, mc.x, mc.y, mc.s, mc.cyc))
            mc.run_until(mc.cyc + 1)
            n += 1
        print('no halt within %d cycles (%d instructions)' % (mx, n))
    except S.Halt as e:
        print('HALT', e, 'after', n, 'instructions')
    for pc, a, x, y, s, c in hist:
        f = src.get(pc, ('?', 0, ''))
        print('%04X A=%02X X=%02X Y=%02X S=%02X %10d  %s:%d  %s'
              % (pc, a, x, y, s, c, os.path.basename(f[0]), f[1], f[2][:60]))


if __name__ == '__main__':
    main()
