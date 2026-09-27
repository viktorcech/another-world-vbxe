#!/usr/bin/env python3
"""_bench_intro -- whole-frame bench of the INTRO (awintro.xex) on the fast cycle-counting
6502 (tools/_sim6502.py) with _bench_frame's VBXE model (MEMAC windows, blitter + its
BUSY time, palette, XDL page flips). The xex is loaded like the boot loader does it: data
segments through the CPU memory map (so MEMAC-B segments land in VRAM) and every INI run
on the CPU. The pre-intro menu and the sound player are stubbed (rts); detect_cpu reports
a stock 6502 (poly_bcb_h = 1, half detail) unless --full.

Per displayed frame (op_blit) it records the busy cycles (all but the pacing spins and
wait_vblank), the blitter waits, and a hash of the shown page + palette: VIDEOSHA is the
bit-identity gate.

    python tools/_bench_intro.py [--frames N] [--warm W] [--full] [--top K] [--dump F]
      (--frames 0 = the whole intro, up to intro_done)
      --gate [--save] : compare VIDEOSHA + work with out/bench/intro_base.json
"""
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _bench_frame as B                                          # noqa: E402
import _lst6502 as LS                                             # noqa: E402

SENT = 0xFFF0


class Stop(Exception):
    pass


class Intro(B.Machine):
    def __init__(self, full=False):
        xex = os.environ.get('AWINTRO_XEX', os.path.join(PROJ, 'awintro.xex'))   # override:
        lst = os.environ.get('AWINTRO_LST', os.path.join(PROJ, 'out', 'awintro.lst'))  # a ref build
        super().__init__(xex, os.path.join(PROJ, 'awgame_full.atr'), lst)
        m = self.m
        for a in range(0, 0xC000):
            m[a] = 0
        self.ins = LS.read('intro')
        self.lab = {}
        for i in self.ins:
            for nm in i.labels:
                self.lab.setdefault(nm, i.addr)
        m[SENT] = 0x02                               # host trap: an INI returned
        self.memb = self.mac_ctl = self.mac_bank = 0
        self._pages()
        self._load(open(xex, 'rb').read())
        L = self.lab
        for name in ('snd_settings', 'snd_init', 'snd_play', 'mus_play', 'snd_stop'):
            if name in L:
                m[L[name]] = 0x60
        if full:                                     # (a stock 6502 runs detect_cpu as is:
            p, t = L['detect_cpu'], L.get('poly_bcb_h', 0xB2)   # $E2 = NOP #imm -> half)
            if 'pbh_set' in L:                       # lda #0 / jmp pbh_set (the cell + its
                t = L['pbh_set']                     #   SMC operand copies)
                m[p:p + 5] = bytes([0xA9, 0, 0x4C, t & 0xFF, t >> 8])
            else:
                m[p:p + 6] = bytes([0xA9, 0, 0x8D, t & 0xFF, t >> 8, 0x60])
        self.spin_tab = {}
        self._find_spins()
        self.spins = set(self.spin_tab)
        self.pc = self.run_addr
        self.s = 0xFF
        self.i = 1

    def _load(self, data):
        p = 0
        while p + 4 <= len(data):
            lo = data[p] | (data[p + 1] << 8)
            if lo == 0xFFFF:
                p += 2
                continue
            hi = data[p + 2] | (data[p + 3] << 8)
            p += 4
            n = hi - lo + 1
            seg = data[p:p + n]
            p += n
            if lo == 0x2E2 and n >= 2:
                self._call(seg[0] | (seg[1] << 8))
                continue
            if lo == 0x2E0 and n >= 2:
                self.run_addr = seg[0] | (seg[1] << 8)
                continue
            for k in range(n):
                a = lo + k
                if self.rp[a >> 8] and self.wp[a >> 8]:
                    self.m[a] = seg[k]
                else:
                    self.wr(a, seg[k])

    def _call(self, entry):
        r = SENT - 1
        self.s = 0xFD
        self.m[0x1FE] = r & 0xFF
        self.m[0x1FF] = r >> 8
        self.pc = entry
        try:
            self.run(self.cyc + 50_000_000)
        except Stop:
            return
        raise RuntimeError('INI at $%04X did not return' % entry)

    def trap(self, pc):
        if pc == SENT:
            raise Stop()
        return B.Machine.trap(self, pc)


def bench(frames=100, warm=20, full=False):
    mc = Intro(full)
    L = mc.lab
    ent = L['op_blit']
    assert mc.m[ent] == 0x20                           # jsr pl_byte
    tgt = mc.m[ent + 1] | (mc.m[ent + 2] << 8)
    mc.m[ent] = 0x02
    done = L['intro_done']
    mc.m[done] = 0x02                                  # the intro's end: stop there
    marks = []

    class Mark(Exception):
        pass

    base = mc.trap

    def trap(pc):
        if pc == done:
            mc._post = (mc.s, mc.pc, mc.cyc)
            raise Stop()
        if pc == ent:                                   # emulate the jsr pl_byte
            marks.append(mc.cyc)
            r = ent + 2
            mc.m[0x100 + mc.s] = r >> 8
            mc.s = (mc.s - 1) & 0xFF
            mc.m[0x100 + mc.s] = r & 0xFF
            mc.s = (mc.s - 1) & 0xFF
            mc.pc = tgt
            mc.cyc += 6
            if mc.prof:
                mc.pcyc[ent] += 6
                mc.pcnt[ent] += 1
            mc._post = (mc.s, mc.pc, mc.cyc)
            raise Mark()
        return base(pc)
    mc.trap = trap
    wv = L.get('wait_vblank')
    vbl_wait = (wv + 2, wv + 4) if wv is not None and mc.m[wv + 2] == 0xC5 else ()
    pace, waith = set(), set()
    for t, (br, body, cost) in mc.spin_tab.items():
        rd = [j.arg for j in body if j.mode in ('zp', 'abs')]
        if any(B.RTCLOK <= r <= B.RTCLOK + 2 for r in rd):
            pace.add(t)
        elif any(r == 0xD653 for r in rd):
            waith.add(t)
    video, busy, waits = [], [], []
    prev = None
    total = warm + frames if frames else 1 << 30
    lim = (total + 400) * B.FRAME * 12 if frames else 1 << 62
    t0 = time.time()
    while len(marks) < total + 1 and mc.cyc < lim:
        try:
            mc.run_until(lim)
        except Stop:
            break
        except Mark:
            (mc.s, mc.pc, mc.cyc) = mc._post
            n = len(marks)
            if n == warm + 1:
                mc.prof = True
                mc.pcyc = [0] * 65536
                mc.pcnt = [0] * 65536
                prev = None
            page = mc.flips[-1][1] if mc.flips else 0
            video.append(hashlib.sha1(bytes(mc.vram[page * 65536:page * 65536 + 32000])
                                      + bytes(mc.pal[1])).hexdigest())
            if mc.prof:
                cur = mc.pcyc
                snap = (sum(cur), sum(cur[a] for t in pace for a in B._body_addrs(mc, t))
                        + sum(cur[a] for a in vbl_wait),
                        sum(cur[a] for t in waith for a in B._body_addrs(mc, t)))
                if prev is not None:
                    busy.append(snap[0] - prev[0] - (snap[1] - prev[1]))
                    waits.append(snap[2] - prev[2])
                prev = snap
    return mc, video, busy, waits, time.time() - t0


def main():
    a = sys.argv[1:]

    def opt(name, default, conv=int):
        return conv(a[a.index(name) + 1]) if name in a else default
    frames, warm, top = opt('--frames', 100), opt('--warm', 20), opt('--top', 30)
    dump = opt('--dump', None, str)
    mc, video, busy, waits, secs = bench(frames, warm, '--full' in a)
    n = len(busy)
    b = sum(busy) / max(1, n)
    w = sum(waits) / max(1, n)
    print('intro bench: %d frames measured (%d shown), %.1f s host' % (n, len(video), secs))
    print('  busy cycles / frame      %10.0f' % b)
    print('  blitter-wait cycles/frame%10.0f' % w)
    print('  CPU work / frame         %10.0f' % (b - w))
    vsha = hashlib.sha1(''.join(video).encode()).hexdigest()
    print('VIDEOSHA %s  (%d frames)' % (vsha, len(video)))
    if '--gate' in a:                                  # the whole-intro gate vs the baseline
        bf = os.path.join(PROJ, 'out', 'bench', 'intro_base.json')
        cur = dict(video=vsha, frames=len(video), work=b - w, wait=w)
        if '--save' in a or not os.path.exists(bf):
            json.dump(cur, open(bf, 'w'), indent=1)
            print('intro baseline saved')
        base = json.load(open(bf))
        lf = os.path.join(PROJ, 'out', 'bench', 'intro_last.json')
        if os.path.exists(lf):
            last = json.load(open(lf))
            print('THIS STEP (vs the previous build): %.0f -> %.0f  = %+.0f cycles / frame (x %d frames = %+.0f)'
                  % (last['work'], b - w, b - w - last['work'], len(video),
                     (b - w - last['work']) * len(video)))
        json.dump(cur, open(lf, 'w'), indent=1)
        same = base['video'] == vsha and base['frames'] == len(video)
        print('INTRO work %.0f -> %.0f (%+.0f, %.2f%%)  %s' % (
            base['work'], b - w, b - w - base['work'], 100 * (b - w - base['work']) / base['work'],
            'IDENTICAL' if same else '*** VIDEO DIFF ***'))
        if not same:
            sys.exit(1)
    if dump:
        json.dump({'%d' % k: mc.pcyc[k] / max(1, n) for k in range(65536) if mc.pcyc[k]}, open(dump, 'w'))
        json.dump({'%d' % k: mc.pcnt[k] / max(1, n) for k in range(65536) if mc.pcnt[k]},
                  open(dump.replace('.json', '_cnt.json'), 'w'))
    if top:
        f = B.proc_of(mc.ins)
        per = {}
        for k in range(65536):
            if mc.pcyc[k]:
                per[f(k)] = per.get(f(k), 0) + mc.pcyc[k]
        for p, c in sorted(per.items(), key=lambda x: -x[1])[:top]:
            print('  %-24s %9.0f' % (p, c / max(1, n)))


if __name__ == '__main__':
    main()
