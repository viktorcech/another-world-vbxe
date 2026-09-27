#!/usr/bin/env python3
"""_intro_pads -- assemble the intro to out/_t.* and report every nocross pad (bytes),
the total, and where snd_preview / prv_vt land (they must stay below $4000).

    python tools/_intro_pads.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _lst6502 as LS                                             # noqa: E402

r = subprocess.run([os.path.join(PROJ, 'mads.exe'), 'src/awvbxe.asm', '-d:GAME_SEC=2941',
                    '-o:out/_t.xex', '-l:out/_t.lst'], cwd=PROJ, capture_output=True, text=True)
err = [ln for ln in r.stdout.splitlines() if 'ERROR' in ln]
for ln in err:
    print(ln)
lst = os.path.join(PROJ, 'out', '_t.lst')
ins = sorted((i for i in LS.read('intro', lst) if i.addr is not None), key=lambda i: i.addr)
addrs = [i.addr for i in ins]
tot = 0
for k, i in enumerate(ins):
    if i.data and i.src and 'dta 0' in i.src and i.src.strip().startswith(':'):
        nxt = next((a for a in addrs[k:] if a > i.addr), i.addr)
        print('  pad %3d  $%04X  %s:%d' % (nxt - i.addr, i.addr, i.file, i.line))
        tot += nxt - i.addr
lab = LS.labels('intro', lst)
print('total pad %d ; snd_preview $%04X ; prv_vt end $%04X' % (tot, lab.get('snd_preview', 0),
                                                               lab.get('prv_vt', 0) + 16))
