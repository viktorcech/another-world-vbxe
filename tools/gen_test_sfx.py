#!/usr/bin/env python3
"""gen_test_sfx.py - bake a few GAME sounds into the INTRO build, for the
pre-intro sound menu's test key.

The menu (src/aw_settings.asm) has to answer "is my covox really at $D700?" and
the only honest answer is a sound. It should be a sound from the GAME -- that is
what the user is about to play -- but the menu runs inside awintro.xex, and at
that moment the machine holds intro data only: the game's sounds live on the ATR
in game_parts.bin and are streamed in per part, long after the menu is gone. So
the sounds are baked into the intro build here.

They are real game SFX: type-0 resources that the GAME parts' bytecode requests
via op_sound (make_game_atr.collect_sound_freqs), minus anything the intro
itself already uses, resampled at the freq the game asks for and NORMALISED --
see normalise() for why the raw levels are unusable as a listening test.

Where they go: the MUSIC blob ends part-way through its last VRAM bank ($12) and
the rest of that bank is dead space -- the IRQ stops on a byte count, so nothing
ever reads there. That tail is the one place in this build with kilobytes free
and no owner. Everything lands in ONE bank, so the player needs no bank walk.

Emits, and the SPLIT matters:
  out/test_sfx.bin           the blob
  src/aw_test_sfx.inc        TST_COUNT + per-sound {window, len} -- icl'd from
                             aw_sound.asm, i.e. into CPU RAM beside the other
                             sound tables
  src/aw_test_sfx_data.inc   the INI chunk that streams the blob -- icl'd from
                             aw_data.asm, where the org is a VRAM WINDOW address

Keeping the tables out of that second file is not tidiness. Emitted there they
assemble at the window address the blob is about to occupy, so at run time the
6502 reads them back as sample bytes: random windows, random lengths, and the
test sound comes out as a rattle on every output. (It did.)

Run after gen_intro_sfx.py (it reads out/intro_music.bin and the intro's map).
"""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import aw_pack                                                    # noqa: E402
from gen_intro_sfx import sound_pcm, resample, pack_nibbles, POKEY_RATE  # noqa: E402
from extract_intro_audio import FREQ_TABLE                        # noqa: E402

OUT = os.path.join(ROOT, 'out')

MUS_BANKS = [0x02, 0x03, 0x06, 0x07, 0x0A, 0x0B, 0x11, 0x12]   # aw_data.asm
TAIL_BANK = MUS_BANKS[-1]

MAX_S = 0.45                   # per sound: long enough to recognise, short
MIN_S = 0.12                   #   enough to fit several -- and to not drone on
WANT = 8                       # how many to offer the random pick


def normalise(sig):
    """Lift a resampled sound to full scale before the nibble packing.

    pack_nibbles keeps the top 4 bits of a signed 8-bit sample, i.e. it divides
    whatever amplitude the source has by 16. The game's sounds are QUIET at the
    source -- of the eight picked here six sit at an RMS of 8..25 out of +-128 --
    so after the shift they are a signal of about +-1 nibble: one or two bits of
    sound buried under three bits of quantisation noise. Played back that is not
    the effect, it is a screech, and the menu's test key exists precisely to be
    judged by ear. (It was: "skreky a divne zvuky" on a covox that turned out to
    be wired and working perfectly.)

    In the GAME the plain shift is right: op_sound carries a per-sound volume,
    several voices mix, and the levels are built around that. Here there is no
    mix and no volume -- one sound plays alone, at full volume, to answer "is my
    covox working?" -- so the only thing that matters is that it be clearly
    audible.

    Scale so the 99th percentile of |sample| reaches full scale rather than the
    peak: a single spike defeats peak normalisation (#87 peaks at 71 with an RMS
    of 8, so peak-scaling lifts it by only 1.8x), while clipping the loudest 1%
    costs nothing audible and brings every sound into the same range. Measured
    over the eight baked sounds this moves the nibble RMS from 0.65..3.33 to
    1.78..3.40 -- the quiet ones gain 2-3 bits of real signal.
    """
    a = sorted(abs(s) for s in sig)
    if not a:
        return sig
    ref = max(1.0, a[int(0.99 * (len(a) - 1))])     # pack_nibbles clamps the rest
    g = 127.0 / ref
    return [s * g for s in sig]


def main():
    music = os.path.join(OUT, 'intro_music.bin')
    if not os.path.exists(music):
        sys.exit('ERROR: out/intro_music.bin missing -- run render_intro_audio.py')
    mus_len = os.path.getsize(music)
    used = mus_len - (len(MUS_BANKS) - 1) * 0x4000          # bytes used in $12
    if used < 0 or used > 0x4000:
        sys.exit(f'ERROR: the music does not end inside bank ${TAIL_BANK:02X} '
                 f'({mus_len} B over {len(MUS_BANKS)} banks) -- re-do the map')
    room = 0x4000 - used

    # --- which sounds does the GAME ask for, and at what pitch? --------------
    import make_game_atr
    bag = make_game_atr.collect_sound_freqs()
    freqs = {}
    for part_combos in bag.values():
        for res, fq in part_combos:
            freqs.setdefault(res, set()).add(min(len(FREQ_TABLE) - 1, fq))

    intro_res = set()
    imap = os.path.join(OUT, 'intro_sfx_map.json')
    if os.path.exists(imap):
        intro_res = {int(k.split(',')[0]) for k in json.load(open(imap))}

    mem = aw_pack.read_memlist()
    cand = []
    for res in sorted(freqs):
        if res in intro_res or res >= len(mem) or mem[res].type != 0:
            continue
        data, _ = aw_pack.load_resource(mem[res])
        if len(data) < 8:
            continue                                        # empty placeholder
        sig, _, _ = sound_pcm(data)
        fq = sorted(freqs[res])[len(freqs[res]) // 2]        # its median pitch
        rate = FREQ_TABLE[fq]
        if len(sig) / rate < MIN_S:
            continue                                        # a 50 ms click
        src = sig[:int(MAX_S * rate) + 1]
        packed = pack_nibbles(normalise(
            resample(src, rate, int(len(src) * POKEY_RATE / rate))))
        cand.append((res, fq, packed))

    if not cand:
        sys.exit('ERROR: no game sound survived the filters -- is orig/ complete?')

    # Spread the pick across the resource list rather than taking the first N:
    # consecutive ids tend to be one scene's variations of the same noise.
    step = max(1, len(cand) // WANT)
    picked, blob = [], bytearray()
    for c in cand[::step]:
        if len(blob) + len(c[2]) > room or len(picked) >= WANT:
            break
        picked.append((c[0], c[1], len(blob), len(c[2])))
        blob += c[2]

    if not picked:
        sys.exit(f'ERROR: not one sound fits the {room} B tail of bank '
                 f'${TAIL_BANK:02X} -- lower MAX_S')

    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, 'test_sfx.bin'), 'wb').write(bytes(blob))

    win = 0x4000 + used                                     # window addr of the tail
    head = ['; auto-generated by tools/gen_test_sfx.py - DO NOT EDIT',
            f'; {len(picked)} GAME sounds for the pre-intro menu test key, baked at '
            f'{POKEY_RATE:.0f} Hz,',
            f'; {len(blob)} of the {room} B free at the end of VRAM bank '
            f'${TAIL_BANK:02X} (behind the music).',
            '; resources: ' + ', '.join(f'#{r} @freq {f}' for r, f, _, _ in picked)]

    # --- tables: CPU RAM, icl'd from aw_sound.asm beside the SFX tables -------
    L = head + [
        f'TST_COUNT = {len(picked)}',
        f'TST_BANK  = ${0x80 | TAIL_BANK:02X}',
        'tst_winlo   dta ' + ','.join(f'${(win + o) & 0xFF:02X}' for _, _, o, _ in picked),
        'tst_winhi   dta ' + ','.join(f'${(win + o) >> 8:02X}' for _, _, o, _ in picked),
        'tst_lenlo   dta ' + ','.join(f'${n & 0xFF:02X}' for _, _, _, n in picked),
        'tst_lenhi   dta ' + ','.join(f'${n >> 8:02X}' for _, _, _, n in picked)]
    open(os.path.join(ROOT, 'src', 'aw_test_sfx.inc'), 'w').write('\n'.join(L) + '\n')

    # --- the load-time stream: icl'd from aw_data.asm, where the org is a VRAM
    #     window address. NOTHING but this chunk may live in that file -- see the
    #     module docstring for what a stray table there sounds like.
    D = head + [
        '        org $0600',
        f'?tst{TAIL_BANK:02X} lda #$80+${TAIL_BANK:02X}',
        '        sta VBXE_MEMAC_B',
        '        rts',
        f'        ini ?tst{TAIL_BANK:02X}',
        f'        org DATAW+{used}',
        f"        ins 'out/test_sfx.bin', 0, {len(blob)}"]
    open(os.path.join(ROOT, 'src', 'aw_test_sfx_data.inc'), 'w').write('\n'.join(D) + '\n')

    print(f'  out/test_sfx.bin + the two .inc : {len(picked)} game sounds, '
          f'{len(blob)} B in bank ${TAIL_BANK:02X} tail ({room} B free)')
    for r, f, o, n in picked:
        print(f'    res #{r:<4} freq {f:<3} {n:5} B  {n * 2 / POKEY_RATE:.2f} s')


if __name__ == '__main__':
    main()
