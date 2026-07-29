#!/usr/bin/env python3
"""dump_intro_snd.py - trace every op_sound/op_music event of the intro VM and
compare against what the Atari build actually plays (out/intro_sfx_map.json).

Reports, per resource: event count, the freq/vol/channel SPREAD (the 6502
player currently plays ONE baked pitch at full volume -- any spread here is
fidelity lost on the Atari), first-event time, and whether aw_playlist.py
ships or DROPS the resource. Then lists dropped events and vol=0 (stop)
events individually.

Usage:  python tools/dump_intro_snd.py
"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import aw_sim

def main():
    events = []
    def hs(self):
        r = self.w(); fq = self.b(); v = self.b(); c = self.b()
        events.append((len(self.frames), 'snd', r, fq, v, c))
    def hm(self):
        r = self.w(); d = self.w(); p = self.b()
        events.append((len(self.frames), 'mus', r, d, p, 0))
    aw_sim.VM.OPS = list(aw_sim.VM.OPS)
    aw_sim.VM.OPS[0x18] = hs
    aw_sim.VM.OPS[0x1A] = hm
    vm = aw_sim.VM('int'); vm.run(100000)

    holds = [max(1, fr[2]) for fr in vm.frames]
    t = [0.0] * (len(holds) + 1)
    for i, h in enumerate(holds):
        t[i + 1] = t[i] + h / 50.0
    def ftime(f):
        return t[min(f, len(holds))]

    sfxmap = json.load(open(os.path.join(PROJ, 'out', 'intro_sfx_map.json')))
    shipped_res = set(int(k.split(',')[0]) for k in sfxmap)
    snd = [e for e in events if e[1] == 'snd']
    print(f'intro: {len(holds)} frames, {t[-1]:.1f} s ; '
          f'{len(snd)} op_sound events ; sfx map ships {len(sfxmap)} variants '
          f'of {len(shipped_res)} resources')

    print('\n=== op_music events ===')
    for (f, k, r, d, p, _) in events:
        if k == 'mus':
            print(f'  frame {f:5}  t={ftime(f):7.1f}s  res={r} delay={d} pos={p}')

    print('\n=== per-resource op_sound summary ===')
    byres = {}
    for (f, k, r, fq, v, c) in snd:
        byres.setdefault(r, []).append((f, fq, v, c))
    for r in sorted(byres):
        evs = byres[r]
        freqs = sorted(set(e[1] for e in evs))
        vols = sorted(set(e[2] for e in evs))
        miss = [fq for fq in freqs if f'{r},{min(39, fq)}' not in sfxmap]
        ship = ('SHIPPED' if not miss else
                f'** DROPPED (freqs {miss}) **' if r not in shipped_res or len(miss) == len(freqs)
                else f'** PARTIAL: freqs {miss} dropped **')
        print(f'  res {r:#04x}  n={len(evs):3}  freqs={freqs} vols={vols}'
              f'  first@{ftime(evs[0][0]):6.1f}s  {ship}')

    print('\n=== dropped events ===')
    drops = [(f, r, fq, v, c) for (f, k, r, fq, v, c) in snd
             if f'{r},{min(39, fq)}' not in sfxmap]
    for (f, r, fq, v, c) in drops:
        print(f'  res {r:#04x}  frame {f:5}  t={ftime(f):7.1f}s  freq={fq} vol={v} ch={c}')
    if not drops:
        print('  none')

    print('\n=== vol=0 (channel stop) events ===')
    stops = [(f, r) for (f, k, r, fq, v, c) in snd if v == 0]
    print('  ' + (', '.join(f'res {r:#04x}@frame {f}' for f, r in stops) if stops else 'none'))

if __name__ == '__main__':
    main()
