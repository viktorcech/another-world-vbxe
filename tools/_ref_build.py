#!/usr/bin/env python3
"""_ref_build -- the REFERENCE build the per-file verifiers compare against: the
current sources (optionally with some text substitutions applied, e.g. to rebuild the
state before an edit) assembled into out/bench/ref/ (awgame.xex + awgame.lst, and the
intro's awintro.xex + awintro.lst). The sources themselves are never touched.

    python tools/_ref_build.py                 # ref = the sources as they are now
    import _ref_build; _ref_build.build(subs=[(path, new_text, old_text), ...])
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
REF = os.path.join(PROJ, 'out', 'bench', 'ref')
BINS = ('fmul.bin', 'water_pal.bin', 'polylut.bin', 'recip.bin', 'intro_music.bin', 'intro_pal.bin',
        'intro_playlist.bin', 'intro_poly.bin', 'intro_sfx.bin', 'test_sfx.bin')


def build(subs=()):
    tmp = os.path.join(REF, 'tree')
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    for d in ('src', 'src_game', 'out'):
        os.makedirs(os.path.join(tmp, d))
    for d in ('src', 'src_game'):
        for fn in os.listdir(os.path.join(PROJ, d)):
            if fn.endswith(('.asm', '.inc')):
                shutil.copy(os.path.join(PROJ, d, fn), os.path.join(tmp, d, fn))
    for b in BINS:
        if os.path.exists(os.path.join(PROJ, 'out', b)):
            shutil.copy(os.path.join(PROJ, 'out', b), os.path.join(tmp, 'out', b))
    for path, new, old in subs:                  # rebuild the text as it was: new -> old
        p = os.path.join(tmp, path)
        s = open(p, encoding='latin-1').read()
        assert new in s, (path, new[:60])
        open(p, 'w', encoding='latin-1').write(s.replace(new, old, 1))
    mads = os.path.join(PROJ, 'mads.exe')
    for src, xex, lst in (('src_game/awgame.asm', 'awgame.xex', 'awgame.lst'),
                          ('src/awvbxe.asm', 'awintro.xex', 'awintro.lst')):
        r = subprocess.run([mads, src, '-d:GAME_SEC=' + os.environ.get('AW_GAME_SEC', '2939'), '-o:' + xex, '-l:' + lst], cwd=tmp,
                           capture_output=True, text=True)
        if not os.path.exists(os.path.join(tmp, xex)):
            print(r.stdout[-1500:])
            raise SystemExit('reference build failed: ' + src)
        shutil.copy(os.path.join(tmp, xex), os.path.join(REF, xex))
        shutil.copy(os.path.join(tmp, lst), os.path.join(REF, lst))
    shutil.rmtree(tmp)
    return REF


if __name__ == '__main__':
    os.makedirs(REF, exist_ok=True)
    print('reference build ->', build())
