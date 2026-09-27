import sys, os
sys.path.insert(0, 'tools')
import _bench_frame as B, _sim6502 as S
from collections import deque
mc = B.Machine('awgame.xex', 'awgame_full.atr', 'out/awgame.lst')
src = {i.addr: (i.file, i.line, i.src) for i in mc.ins}
hist = deque(maxlen=60)
n = 0
try:
    while True:
        hist.append((mc.pc, mc.a, mc.x, mc.y, mc.s, mc.cyc))
        mc.run_until(mc.cyc + 1)
        n += 1
except S.Halt as e:
    print('HALT', e, 'after', n)
    for pc, a, x, y, s, c in hist:
        f = src.get(pc, ('?', 0, ''))
        print('%04X A=%02X X=%02X Y=%02X S=%02X %9d  %s:%d %s' % (pc, a, x, y, s, c, os.path.basename(f[0]), f[1], f[2][:60]))
