#!/usr/bin/env python3
"""render_intro_audio.py - render AW music #7 (MUSIC ONLY, no SFX baked in) to
one mono 4-bit POKEY stream for the intro's second sample voice.

History: the first attempt PRE-MIXED music+SFX into one stream; it was removed
because the baked SFX drifted against the deadline-paced visuals (renders that
overrun stretch the visual timeline while audio runs at a fixed ~3995 Hz). Now
the stream carries the music alone and FREE-RUNS on AUDC2 (drift is harmless
for background music); SFX stay discrete playlist events on AUDC4, so they
keep exact sync. See memory intro-sound-scope.

Timeline: run the intro VM (aw_sim), find the music segment using the rawgl
op_music semantics (res!=0 = start, its delay overrides the module tempo;
res==0 && delay!=0 = TEMPO CHANGE mid-piece; res==0 && delay==0 = stop --
_rawgl_script.cpp snd_playMusic). The intro plays #7 for ~117 s with one
tempo change at ~45 s; treating the tempo change as a stop was the old "music
is short" bug. Frame -> seconds via the per-frame hold (VAR_PAUSE_SLICES /
frameHz). Music #7 is rendered with the AW SfxPlayer model (4 sample voices,
Amiga period -> freq = kPaulaFreq/(period*2), tempo = delay rows), then
downsampled to HALF the POKEY tick rate (the 6502 voice advances every other
Timer-1 tick: 117 s @ 3995 Hz = 234 KB would not fit the free VRAM banks;
@ ~1998 Hz it is ~118 KB in 8 banks) and packed to 4-bit nibbles.

Outputs:  out/intro_music.bin        (POKEY 4-bit packed, streamed to VRAM)
          out/audio/intro_music.wav  (audition)
          src/aw_music_len.inc       (MUSIC_LEN for the 6502 player)
"""
import os, sys, struct, wave
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aw_pack, aw_sim

OUT = os.path.join(os.path.dirname(HERE), 'out', 'audio')   # WAV audition lives here
OUT_ROOT = os.path.join(os.path.dirname(HERE), 'out')        # .bin/.json the build reads
RATE = 22050                     # PC mix rate (downsampled to the POKEY rate at the end)
POKEY_RATE = 3995                # AUDF1=15 Timer-1 tick rate on the Atari
MUS_RATE = POKEY_RATE / 2        # the music voice advances every OTHER tick
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


def render_music(buf, t_start, t_stop, byid, tempo=None):
    """Render AW music #7 into buf[] (float, RATE) over [t_start, t_stop) seconds.
    tempo = [(t_rel_seconds, delay), ...] -- the op_music tempo timeline (a delay
    of 0/None means the module's default). rawgl SfxPlayer: delay -> ms per row
    = delay*60/7050."""
    data, _ = aw_pack.load_resource(byid[MUSIC_RES])
    delay_def = be16(data, 0)
    numorder = be16(data, 0x3E)
    order = list(data[0x40:0x40 + numorder])
    # 15 instruments: {resId, volume}
    inst = []
    for i in range(15):
        rid = be16(data, 2 + i * 4); vol = be16(data, 2 + i * 4 + 2)
        inst.append((rid, vol, load_sound(byid, rid) if rid else None))

    def spr(delay):
        return max(1, int(round(((delay or delay_def) * 60 / 7050) * RATE / 1000.0)))
    tempo_ev = [(tt, spr(d)) for tt, d in (tempo or [(0.0, None)])]
    samples_per_row = tempo_ev[0][1]
    next_te = 1
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
        if next_te < len(tempo_ev) and (i - i0) >= int(tempo_ev[next_te][0] * RATE):
            samples_per_row = tempo_ev[next_te][1]; next_te += 1
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


MUSIC_VRAM_BANKS = 8           # banks $02,$03,$06,$07,$0A,$0B,$11,$12 (aw_data.asm
                               # chunks + mus_banks in aw_sound.asm, in this order)


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

    # Music segment via the rawgl op_music semantics (snd_playMusic): res!=0 =
    # START (delay overrides the module tempo when nonzero); res==0 && delay!=0
    # = TEMPO CHANGE; res==0 && delay==0 = STOP. MUSIC ONLY -- no SFX baked in:
    # the stream free-runs on its own POKEY channel while SFX stay discrete
    # playlist events (0x08).
    mus = [(ftime(f), f, r, d) for (f, k, r, d, p) in events if k == 'mus']
    start = next(((t, f, d) for t, f, r, d in mus if r == MUSIC_RES), None)
    if start is None:
        sys.exit('ERROR: no op_music start event found in the intro trace')
    t0, f0, delay0 = start
    tempo = [(0.0, delay0)]                    # buf-relative tempo timeline
    t_stop, seg_end_frame = total, len(holds)
    for t, f, r, d in mus:
        if t <= t0 or r != 0:
            continue
        if d:
            tempo.append((t - t0, d))          # tempo change, the music keeps playing
        else:
            t_stop, seg_end_frame = t, f       # the real stop
            break

    seg_len = t_stop - t0
    buf = [0.0] * int(seg_len * RATE + RATE)
    render_music(buf, 0.0, seg_len, byid, tempo)   # music from the segment start

    # normalize, downsample RATE -> MUS_RATE (half a tick rate: the 6502 voice
    # advances every other Timer-1 IRQ), quantize to 4-bit, pack hi/lo
    peak = max((abs(x) for x in buf), default=1.0) or 1.0
    g = 0.95 / peak
    step = RATE / MUS_RATE
    n_out = int(len(buf) / step)
    q = []
    for i in range(n_out):
        s = buf[int(i * step)] * g
        v = int((s * 0.5 + 0.5) * 15 + 0.5)
        q.append(max(0, min(15, v)))
    if len(q) & 1:
        q.append(8)
    packed = bytes((q[i] << 4) | q[i + 1] for i in range(0, len(q), 2))

    cap = MUSIC_VRAM_BANKS * 0x4000
    if len(packed) > cap:
        sys.exit(f'ERROR: music stream {len(packed)} B > {cap} B '
                 f'({MUSIC_VRAM_BANKS} VRAM gap banks) -- extend mus_banks in '
                 f'aw_sound.asm + the chunks in aw_data.asm first')

    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT_ROOT, 'intro_music.bin'), 'wb').write(packed)   # streamed to VRAM
    with wave.open(os.path.join(OUT, 'intro_music.wav'), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(1); w.setframerate(int(round(MUS_RATE)))
        w.writeframes(bytes((v << 4) | 0x08 for v in q))
    # length (bytes) for the 6502 player's 24-bit countdown; the meta json is
    # informational only (nothing suppresses SFX any more -- they play discrete).
    inc = os.path.join(os.path.dirname(HERE), 'src', 'aw_music_len.inc')
    open(inc, 'w').write(f'; auto-generated by render_intro_audio.py\nMUSIC_LEN = {len(packed)}\n')
    open(os.path.join(OUT_ROOT, 'intro_music_meta.json'), 'w').write(
        f'{{"seg_end_frame": {seg_end_frame}, "bytes": {len(packed)}}}')

    print(f"intro total    : {total:6.1f} s ; music segment: {seg_len:5.1f} s "
          f"(frames {f0}..{seg_end_frame}, {len(tempo)-1} tempo change(s))")
    print(f"music stream @ {MUS_RATE:.1f} Hz 4-bit: {len(packed)} bytes "
          f"({len(packed)/1024:.1f} KB, {(len(packed)+0x3FFF)>>14} VRAM banks)")
    print(f"  out/audio/intro_music.wav + intro_music.bin, src/aw_music_len.inc")


if __name__ == '__main__':
    main()
