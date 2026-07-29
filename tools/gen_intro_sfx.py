#!/usr/bin/env python3
"""gen_intro_sfx.py - bake the intro's SFX pitch VARIANTS for the VRAM POKEY player.

The 6502 plays every sample at the fixed Timer-1 tick (~3995 Hz): the music
voice shares the timer, so AUDF1 cannot vary per sound the way the game build
does. Pitch is therefore baked OFFLINE: one variant per distinct (resource,
freq) combo the intro's op_sound trace requests, resampled from the original
8-bit PCM to 3995 Hz at the AW frequency-table rate. Volume is NOT baked --
the player scales nibbles at run time through a 16-level volume-curve table
(snd_vt at $0600, filled by snd_play from voltab; playlist opcode 0x08 ships
idx,vol).

Variants are truncated to their maximum AUDIBLE length: the player is ONE
channel, latest-wins, so an event is heard only until the NEXT op_sound event
(any resource) or its own end. Looped resources are unrolled up to that
window. This keeps the low-pitch (= time-stretched) sweep variants small.

Emits:
  out/intro_sfx.bin       the variant blob (loaded to VRAM by src/aw_sfx_data.inc)
  src/aw_sfx_tables.inc   per-variant {bank, bank-list idx, window, len} + sfx_blist + voltab
  src/aw_sfx_data.inc     INI chunk directives loading the blob (icl'd by aw_data.asm)
  out/intro_sfx_map.json  "res,freq" -> variant index (read by aw_playlist.py)
"""
import os, sys, json, struct
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import aw_pack, aw_sim
from extract_intro_audio import FREQ_TABLE

OUT = os.path.join(ROOT, 'out')

POKEY_RATE = 3995.0            # AUDF1=15 Timer-1 tick rate (SFX advance every tick)
FRAME_HZ = 50                  # PAL pacing (event times from VAR_PAUSE_SLICES holds)
TAIL_S = 0.20                  # safety tail past the next-event cut
MIN_S = 0.15                   # floor for instantly-replaced events
MAX_S = 6.0                    # unroll cap for looped sounds heard to the end

# The blob's VRAM bank list (16 KB each, NOT contiguous): the page-3 gap
# ($0E,$0F), the gap after the music tail ($13; music sits in $02..$0B,$11,$12),
# and the last free bank ($1F). The 6502 walks sfx_blist on window crossings.
BANKS = [0x0E, 0x0F, 0x13, 0x1F]


def be16(b, o):
    return struct.unpack_from('>H', b, o)[0]


def sound_pcm(data):
    """AW sound resource -> (signed samples list, lead_samples, loop_samples)."""
    ln = be16(data, 0); loop = be16(data, 2)
    body = data[8:8 + (ln + loop) * 2]
    sig = [b - 256 if b >= 128 else b for b in body]
    return sig, ln * 2, loop * 2


def resample(src, rate, out_n):
    """src (signed) at `rate` Hz -> out_n samples at POKEY_RATE. Box mean when
    decimating, linear interpolation when (slightly) upsampling."""
    step = rate / POKEY_RATE
    out = []
    if step >= 1.0:
        for i in range(out_n):
            j0 = int(i * step)
            j1 = min(max(j0 + 1, int((i + 1) * step)), len(src))
            if j0 >= len(src):
                break
            out.append(sum(src[j0:j1]) / (j1 - j0))
    else:
        for i in range(out_n):
            p = i * step
            ip = int(p)
            if ip >= len(src):
                break
            s1 = src[ip + 1] if ip + 1 < len(src) else src[ip]
            out.append(src[ip] + (s1 - src[ip]) * (p - ip))
    return out


def pack_nibbles(samples):
    nibs = [max(0, min(15, (int(round(s)) + 128) >> 4)) for s in samples]
    if len(nibs) & 1:
        nibs.append(8)
    return bytes((nibs[i] << 4) | nibs[i + 1] for i in range(0, len(nibs), 2))


def trace_events():
    """(t_seconds, res, freq, vol) for every op_sound, in playlist order."""
    events = []
    def hs(self):
        r = self.w(); fq = self.b(); v = self.b(); c = self.b()
        events.append((len(self.frames), r, fq, v))
    aw_sim.VM.OPS = list(aw_sim.VM.OPS)
    aw_sim.VM.OPS[0x18] = hs
    vm = aw_sim.VM('int'); vm.run(100000)
    holds = [max(1, fr[2]) for fr in vm.frames]
    t = [0.0] * (len(holds) + 1)
    for i, h in enumerate(holds):
        t[i + 1] = t[i] + h / FRAME_HZ
    return [(t[min(f, len(holds))], r, fq, v) for (f, r, fq, v) in events], t[-1]


def main():
    events, total = trace_events()
    mem = aw_pack.read_memlist()

    # per-(res,freq) needed seconds: audible window = until the NEXT snd event
    need = {}
    for i, (t, r, fq, v) in enumerate(events):
        nxt = events[i + 1][0] if i + 1 < len(events) else total + 1.0
        aud = max(MIN_S, min(MAX_S, (nxt - t) + TAIL_S))
        key = (r, min(39, fq))
        need[key] = max(need.get(key, 0.0), aud)

    pcm_cache = {}
    for r in sorted(set(r for r, _ in need)):
        data, _ = aw_pack.load_resource(mem[r])
        pcm_cache[r] = sound_pcm(data) if len(data) >= 8 else None
        if pcm_cache[r] is None:
            print(f'  note: res {r:#04x} is EMPTY in the source data (placeholder) '
                  f'-> omitted; aw_playlist drops its events')

    variants = sorted(k for k in need if pcm_cache[k[0]] is not None)
    blob = bytearray()
    starts, lens = [], []
    per_res = {}
    for (r, fq) in variants:
        sig, lead, loop = pcm_cache[r]
        rate = FREQ_TABLE[fq]
        need_native = int(need[(r, fq)] * rate) + 1
        if loop > 0 and len(sig) < need_native:      # unroll the looped tail
            src = list(sig)
            while len(src) < need_native:
                src += sig[lead:lead + loop]
            src = src[:need_native]
        else:
            src = sig[:need_native]
        out_n = int(len(src) * POKEY_RATE / rate)
        packed = pack_nibbles(resample(src, rate, out_n))
        starts.append(len(blob))
        lens.append(len(packed))
        blob += packed
        per_res.setdefault(r, []).append((fq, len(packed)))

    cap = len(BANKS) * 0x4000
    if len(blob) > cap:
        sys.exit(f'ERROR: sfx blob {len(blob)} B > {cap} B ({len(BANKS)} banks '
                 f'{["$%02X" % b for b in BANKS]}) -- lower MAX_S/TAIL_S or merge freqs')
    if len(variants) > 255:
        sys.exit(f'ERROR: {len(variants)} variants > 255 (1-byte playlist idx)')

    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, 'intro_sfx.bin'), 'wb').write(bytes(blob))

    # ---- asm tables -----------------------------------------------------
    def bankwin(off):
        li = off >> 14                               # index into BANKS
        win = 0x4000 | (off & 0x3FFF)
        return li, 0x80 | BANKS[li], win & 0xFF, win >> 8

    sb = [bankwin(a) for a in starts]
    # voltab: 16 volume curves x 16 nibbles, pre-ORed with $10 (AUDC volume-only
    # bit). Row L covers vols 4L..4L+3; row 15 is exactly 1.0 (bit-identical
    # passthrough), row 0 is near-silence.
    vt = []
    for L in range(16):
        g = min(63, 4 * L + 3) / 63.0
        vt.append([0x10 | max(0, min(15, 8 + int(round((n - 8) * g)))) for n in range(16)])

    L = ['; auto-generated by tools/gen_intro_sfx.py - DO NOT EDIT',
         f'; {len(variants)} intro SFX variants (res,freq -> pitch baked at '
         f'{POKEY_RATE:.0f} Hz), {len(blob)} bytes',
         f'; in VRAM banks {" ".join("$%02X" % b for b in BANKS)} '
         f'(walked via sfx_blist on window crossings)',
         f'SFX_COUNT = {len(variants)}',
         'sfx_blist   dta ' + ','.join(f'${0x80|b:02X}' for b in BANKS),
         'sfx_bank    dta ' + ','.join(f'${b:02X}' for _, b, _, _ in sb),
         'sfx_bidx    dta ' + ','.join(f'{li}' for li, _, _, _ in sb),
         'sfx_winlo   dta ' + ','.join(f'${l:02X}' for _, _, l, _ in sb),
         'sfx_winhi   dta ' + ','.join(f'${h:02X}' for _, _, _, h in sb),
         '; byte lengths (the player counts a NEGATED copy up to 0)',
         'sfx_lenlo   dta ' + ','.join(f'${l & 0xFF:02X}' for l in lens),
         'sfx_lenhi   dta ' + ','.join(f'${l >> 8:02X}' for l in lens),
         '; 16 volume curves x 16 nibbles ($10-ORed); snd_play copies row',
         '; vol>>2 to snd_vt ($0600), the IRQ output goes through it (SMC).',
         'voltab']
    for row in vt:
        L.append('        dta ' + ','.join(f'${v:02X}' for v in row))
    open(os.path.join(ROOT, 'src', 'aw_sfx_tables.inc'), 'w').write('\n'.join(L) + '\n')

    # ---- INI load chunks (16 KB window per bank, exact byte counts) ------
    D = ['; auto-generated by tools/gen_intro_sfx.py - DO NOT EDIT',
         f'; stream out/intro_sfx.bin ({len(blob)} B) into VRAM banks '
         f'{" ".join("$%02X" % b for b in BANKS)}']
    for k, bank in enumerate(BANKS):
        off = k * 0x4000
        if off >= len(blob):
            break
        n = min(0x4000, len(blob) - off)
        D += ['        org $0600',
              f'?sfx{bank:02X} lda #$80+${bank:02X}',
              '        sta VBXE_MEMAC_B',
              '        rts',
              f'        ini ?sfx{bank:02X}',
              '        org DATAW',
              f"        ins 'out/intro_sfx.bin', ${off:05X}, {n}"]
    open(os.path.join(ROOT, 'src', 'aw_sfx_data.inc'), 'w').write('\n'.join(D) + '\n')

    json.dump({f'{r},{fq}': i for i, (r, fq) in enumerate(variants)},
              open(os.path.join(OUT, 'intro_sfx_map.json'), 'w'))

    print(f'intro SFX: {len(variants)} variants, {len(blob)} bytes '
          f'({len(blob)/1024:.1f} KB / {cap//1024} KB cap, '
          f'{(len(blob)+0x3FFF)>>14} of {len(BANKS)} banks)')
    for r in sorted(per_res):
        vs = per_res[r]
        print(f'  res {r:#04x}: {len(vs):2} variant(s), {sum(n for _, n in vs):6} B  '
              f'freqs {sorted(f for f, _ in vs)}')
    print('  out/intro_sfx.bin, src/aw_sfx_tables.inc, src/aw_sfx_data.inc, '
          'out/intro_sfx_map.json')


if __name__ == '__main__':
    main()
