#!/usr/bin/env python3
"""Pick visually interesting frames from each Another World part (oracle sim)."""
import os, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import aw_sim, game_sim

PARTS = {16002: 'water', 16003: 'jail', 16004: 'cite', 16005: 'arene', 16006: 'luxe', 16007: 'final'}

def score(page):
    c = collections.Counter(page)
    nz = len(page) - c.get(0, 0)
    return len(c) * 1000 + nz // 100

def scan(name, frames, every=1):
    print(f"--- {name}: {len(frames)} frames")
    best = []
    for i, (page, pal, hold, draws, dl) in enumerate(frames):
        if i % every: continue
        best.append((score(page), i, len(set(page)), draws))
    best.sort(reverse=True)
    for s, i, d, dr in best[:12]:
        print(f"   f{i:5} colours={d:2} draws={dr:3} score={s}")

frames, _ = aw_sim.render_intro(2000, 'int')
scan('intro', frames, 7)
for p, nm in PARTS.items():
    try:
        vm = game_sim.GameVM(p)
        fr = vm.run(260)
        scan(f'{p} {nm}', fr, 5)
    except Exception as e:
        print(f'   {p} {nm}: FAILED {e}')
