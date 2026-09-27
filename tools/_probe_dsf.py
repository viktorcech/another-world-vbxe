#!/usr/bin/env python3
"""_probe_dsf -- does real game data ever reach draw_scanline_fast with an edge outside
[$8000,$813F] (x outside 0..319)? The fast path is chosen from the shape's bbox; this
traps its entry on every part and counts the violations.

    python tools/_probe_dsf.py [frames]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _bench_frame as B                                          # noqa: E402

PARTS = [16001, 16002, 16003, 16004, 16005, 16006, 16007]


def main():
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    tot = bad = 0
    orig_init = B.Machine.__init__

    def init(self, *a, **k):
        orig_init(self, *a, **k)
        L = self.lab
        ent0 = L['draw_scanline_fast']
        assert self.m[ent0] == 0xA6 and self.m[ent0 + 4] == 0x4A   # ldx zp / lda zp / lsr @
        ent = ent0 + 4             # (the first 3 bytes are re-patched by set_render_mode)
        zp = {'cr2': 0xC2, 'cr3': 0xC3, 'cl2': 0xC6, 'cl3': 0xC7,   # src/aw_equates.inc
              'hy_lo': self.m[ent0 + 1]}                          # ldx hy_lo's operand
        self.m[ent] = 0x02
        st = self._probe = [0, 0, set()]
        self._probe_ent = ent
        self._probe_zp = zp

    orig_trap = B.Machine.trap

    def trap(self, pc):
            ent, zp, st = self._probe_ent, self._probe_zp, self._probe
            if pc != ent:
                return orig_trap(self, pc)
            m = self.m
            for e in (('cr2', 'cr3'), ('cl2', 'cl3')):
                v = m[zp[e[0]]] | (m[zp[e[1]]] << 8)
                if not 0x8000 <= v <= 0x813F:
                    st[1] += 1
                    st[2].add(v)
            st[0] += 1
            self.c = self.a & 1                   # the lsr @ it replaced
            self.a >>= 1
            self.zv = self.nv = self.a
            self.pc = ent + 1
            self.cyc += 2
    B.Machine.__init__ = init
    B.Machine.trap = trap
    for p in PARTS:
        r = B.frame_bench(None, frames, 20, p, None, prof=False, verbose=False)
        n, b, vals = r['machine']._probe
        tot += n
        bad += b
        print('%d: %6d fast spans, %d edges outside 0..319 %s' % (p, n, b, sorted(vals)[:8]))
    print('total %d spans, %d violations' % (tot, bad))


if __name__ == '__main__':
    main()
