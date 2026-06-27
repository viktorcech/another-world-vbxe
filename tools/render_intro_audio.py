#!/usr/bin/env python3
"""render_intro_audio.py - render the WHOLE intro soundtrack (AW music #7 + all
SFX) to ONE mono stream, mixed offline on the PC at the intro's exact timeline.

Why offline: the intro is fully deterministic (the flattened playlist fixes every
sound's time), so we can pre-mix music+SFX into a single mono 4-bit stream and let
the existing 1-voice POKEY player replay it -- no runtime multi-voice mixing.

Timeline: run the intro VM (aw_sim), stamp every op_music/op_sound by the display-
frame counter; frame -> seconds via the per-frame hold (VAR_PAUSE_SLICES / frameHz).
Music #7 is rendered with the AW SfxPlayer model (4 sample voices, Amiga period ->
freq = kPaulaFreq/(period*2), tempo = delay rows). SFX are mixed in at their event
times at getSoundFreq(freq).

Outputs:  out/audio/intro_soundtrack.wav  (audition)
          out/audio/intro_soundtrack_4bit.bin  (POKEY 4-bit packed, for VRAM)
"""
import os, sys, struct, wave
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aw_pack, aw_sim

OUT = os.path.join(os.path.dirname(HERE), 'out', 'audio')   # WAV audition lives here
OUT_ROOT = os.path.join(os.path.dirname(HERE), 'out')        # .bin/.json the build reads
RATE = 22050                     # PC mix rate (downsampled to the POKEY rate at the end)
POKEY_RATE = 3995                # AUDF1=15 playback rate on the Atari
FRAME_HZ = 50                    # PAL pacing (the intro runs at VAR_PAUSE_SLICES/50 s)
kPaulaFreq = 7159092
MUSIC_RES = 7

PERIOD_TABLE = [1076,1016,960,906,856,808,762,720,678,640,604,570,538,508,480,453,
                428,404,381,360,339,320,302,285,269,254,240,226,214,202,190,180,170,
                160,151,143,135,127,120,113]


def be16(b, o):
    return struct.unpack_from('>H', b, o)[0]


def sound_pcm_signed(data):
    """AW sound resource -> (float samples -1..1, loop_start, loop_len) at native rate.
    8-byte header: +0 len words (lead-in), +2 loopLen words; body = 8-bit SIGNED PCM."""
    ln = be16(data, 0); loop = be16(data, 2)
    body = data[8:8 + (ln + loop) * 2]
    f = [((x - 256) if x > 127 else x) / 128.0 for x in body]
    if loop:
        return f, ln * 2, loop * 2
    return f, None, 0


def load_sound(byid, rid):
    data, _ = aw_pack.load_resource(byid[rid])
    if len(data) < 8:
        return None
    return sound_pcm_signed(data)


def render_music(buf, t_start, t_stop, byid):
    """Render AW music #7 into buf[] (float, RATE) over [t_start, t_stop) seconds."""
    data, _ = aw_pack.load_resource(byid[MUSIC_RES])
    delay = be16(data, 0)
    numorder = be16(data, 0x3E)
    order = list(data[0x40:0x40 + numorder])
    # 15 instruments: {resId, volume}
    inst = []
    for i in range(15):
        rid = be16(data, 2 + i * 4); vol = be16(data, 2 + i * 4 + 2)
        inst.append((rid, vol, load_sound(byid, rid) if rid else None))
    # AW SfxPlayer tempo: delay -> ms per row (rawgl: delay*60/7050), then -> samples.
    samples_per_row = max(1, int(round((delay * 60 / 7050) * RATE / 1000.0)))
    patbase = 0xC0
    # 4 channels: each {smp(floats), pos, inc, vol(0..63), loop_start, loop_len, on}
    ch = [dict(smp=None, pos=0.0, inc=0.0, vol=0, ls=None, ll=0, on=False) for _ in range(4)]

    def handle_row(curorder, curpos):
        pat = patbase + order[curorder] * 1024 + curpos
        for c in range(4):
            n1 = be16(data, pat + c * 4); n2 = be16(data, pat + c * 4 + 2)
            if n1 == 0xFFFD:
                continue
            s = (n2 & 0xF000) >> 12
            if s != 0:
                rid, ivol, smp = inst[s - 1]
                m = ivol
                eff = (n2 & 0x0F00) >> 8
                if eff == 5:   m = min(0x3F, m + (n2 & 0xFF))
                elif eff == 6: m = max(0, m - (n2 & 0xFF))
                ch[c]['vol'] = m
                ch[c]['_pending'] = smp
            if n1 == 0xFFFE:
                ch[c]['on'] = False
            elif n1 != 0 and ch[c].get('_pending'):
                smp = ch[c]['_pending']
                ch[c]['smp'], ch[c]['ls'], ch[c]['ll'] = smp
                ch[c]['inc'] = (kPaulaFreq / (n1 * 2)) / RATE
                ch[c]['pos'] = 0.0
                ch[c]['on'] = True

    i0 = int(t_start * RATE)
    i1 = min(len(buf), int(t_stop * RATE))
    curorder, curpos, rowctr = 0, 0, 0
    handle_row(curorder, curpos)
    for i in range(i0, i1):
        acc = 0.0
        for c in ch:
            if not c['on'] or c['smp'] is None:
                continue
            p = c['pos']; smp = c['smp']
            if p >= len(smp):
                if c['ll'] > 0:
                    p = c['ls'] + ((p - c['ls']) % c['ll']); c['pos'] = p
                else:
                    c['on'] = False; continue
            ip = int(p); fr = p - ip
            s1 = smp[ip + 1] if ip + 1 < len(smp) else smp[ip]
            acc += (smp[ip] + (s1 - smp[ip]) * fr) * (c['vol'] / 63.0)
            c['pos'] += c['inc']
        buf[i] += acc
        rowctr += 1
        if rowctr >= samples_per_row:
            rowctr = 0
            curpos += 16
            if curpos >= 1024:
                curpos = 0; curorder += 1
                if curorder >= numorder:
                    curorder = 0          # loop the module until t_stop
            handle_row(curorder, curpos)


def render_sfx(buf, t, freq_byte, vol, smp):
    if smp is None:
        return
    f, ls, ll = smp
    rate = kPaulaFreq / (PERIOD_TABLE[min(39, freq_byte)] * 2)
    inc = rate / RATE
    pos = 0.0; i = int(t * RATE); g = vol / 63.0
    n = len(buf)
    while i < n:
        if pos >= len(f):
            if ll > 0: pos = ls + ((pos - ls) % ll)
            else: break
        buf[i] += f[int(pos)] * g
        pos += inc; i += 1
        if pos >= len(f) and ll == 0:
            break


def main():
    mem = aw_pack.read_memlist(); byid = {m.idx: m for m in mem}
    events = []
    def hs(self):
        r = self.w(); fq = self.b(); v = self.b(); c = self.b()
        events.append((len(self.frames), 'snd', r, fq, v))
    def hm(self):
        r = self.w(); d = self.w(); p = self.b()
        events.append((len(self.frames), 'mus', r, d, p))
    aw_sim.VM.OPS = list(aw_sim.VM.OPS)
    aw_sim.VM.OPS[0x18] = hs; aw_sim.VM.OPS[0x1A] = hm
    vm = aw_sim.VM('int'); vm.run(100000)

    # frame -> start time (seconds) from the per-frame hold (VAR_PAUSE_SLICES)
    holds = [max(1, fr[2]) for fr in vm.frames]
    tstart = [0.0] * (len(holds) + 1)
    for i, h in enumerate(holds):
        tstart[i + 1] = tstart[i] + h / FRAME_HZ
    def ftime(f): return tstart[min(f, len(holds))]
    total = tstart[-1]

    # The MUSIC plays only the first segment: the first op_music(res #7) until the
    # next op_music (a stop). We pre-mix ONLY that segment (music + the SFX that fall
    # inside it) into one stream; SFX after it stay discrete (aw_playlist 0x08).
    mus = [(ftime(f), r, f) for (f, k, r, *_ ) in events if k == 'mus']
    t0 = next((t for t, r, f in mus if r == MUSIC_RES), 0.0)
    after = [t for t, r, f in mus if t > t0]
    t_stop = min(after) if after else total
    seg_end_frame = next((f for tt, r, f in mus if tt == t_stop), len(holds))

    seg_len = t_stop - t0
    buf = [0.0] * int(seg_len * RATE + RATE)
    render_music(buf, 0.0, seg_len, byid)      # music from the segment start (buf-relative)

    sfx_cache = {}
    nsfx = 0
    for (f, k, r, a, b) in events:
        if k != 'snd':
            continue
        et = ftime(f)
        if not (t0 <= et < t_stop):            # only SFX inside the music segment
            continue
        if r not in sfx_cache:
            sfx_cache[r] = load_sound(byid, r)
        render_sfx(buf, et - t0, a, b, sfx_cache[r])
        nsfx += 1

    # normalize, downsample RATE -> POKEY_RATE, quantize to 4-bit, pack hi/lo
    peak = max((abs(x) for x in buf), default=1.0) or 1.0
    g = 0.95 / peak
    step = RATE / POKEY_RATE
    n_out = int(len(buf) / step)
    q = []
    for i in range(n_out):
        s = buf[int(i * step)] * g
        v = int((s * 0.5 + 0.5) * 15 + 0.5)
        q.append(max(0, min(15, v)))
    if len(q) & 1:
        q.append(8)
    packed = bytes((q[i] << 4) | q[i + 1] for i in range(0, len(q), 2))

    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT_ROOT, 'intro_music.bin'), 'wb').write(packed)   # streamed to VRAM
    with wave.open(os.path.join(OUT, 'intro_music.wav'), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(1); w.setframerate(POKEY_RATE)
        w.writeframes(bytes((v << 4) | 0x08 for v in q))
    # length (bytes) + the frame where the music segment ends (aw_playlist suppresses
    # discrete SFX before it -- they are baked into this stream).
    inc = os.path.join(os.path.dirname(HERE), 'src', 'aw_music_len.inc')
    open(inc, 'w').write(f'; auto-generated by render_intro_audio.py\nMUSIC_LEN = {len(packed)}\n')
    open(os.path.join(OUT_ROOT, 'intro_music_meta.json'), 'w').write(
        f'{{"seg_end_frame": {seg_end_frame}, "bytes": {len(packed)}}}')

    print(f"intro total    : {total:6.1f} s ; music segment: {seg_len:5.1f} s "
          f"(frames {next((f for _,r,f in mus if r==MUSIC_RES),0)}..{seg_end_frame})")
    print(f"sfx in segment : {nsfx} (baked in) ; rest stay discrete")
    print(f"music stream @ {POKEY_RATE} Hz 4-bit: {len(packed)} bytes "
          f"({len(packed)/1024:.1f} KB, {(len(packed)+0x3FFF)>>14} VRAM banks)")
    print(f"  out/audio/intro_music.wav + intro_music.bin, src/aw_music_len.inc")


if __name__ == '__main__':
    main()
