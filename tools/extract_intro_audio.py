#!/usr/bin/env python3
"""extract_intro_audio.py - extract the Another World INTRO's audio from the
original DOS banks (orig/ MEMLIST.BIN + BANK0x) via aw_pack.

What the intro (part 16001) actually plays, found by tracing the VM's op_sound
(0x18) / op_music (0x1A) calls (see the module docstring trace):
  * MUSIC : 1 track, resource #7 (a tracker module whose 6 instruments are the
            sound resources 0x01..0x06).
  * SOUND : 18 sound effects (resource ids listed in INTRO_SFX below).

AW sound resource format: 8-byte big-endian header
    +0  uint16  len     (lead-in length in 16-bit WORDS)
    +2  uint16  loopLen (looped tail length in WORDS; 0 = one-shot)
    +4  4 bytes reserved
    +8  ...     8-bit SIGNED PCM, (len+loopLen)*2 bytes
Playback rate = AW frequency table (Hz) indexed by op_sound's freq byte.

AW music module format (type 1):
    +0x00 uint16  default delay (tempo)
    +0x02 15 * {uint16 resId, uint16 volume}   instrument (sample) table
    +0x3E uint16  numOrder
    +0x40 128 b   order table (pattern indices)
    +0xC0 ...     pattern data (64 rows * 4 chan * 4 bytes = 1024 b / pattern)

Outputs to out/audio/ : <id>_sfx.wav per effect, music07_instNN_*.wav per
instrument, music07.bin (raw module), and report.txt. Sounds are written as
8-bit unsigned PCM WAV at their intro playback rate.

Usage:  python tools/extract_intro_audio.py
"""
import os, sys, struct, wave
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aw_pack

OUT = os.path.join(HERE, "..", "out", "audio")

# AW frequency table (Hz) - op_sound's freq byte indexes this (40 entries).
FREQ_TABLE = [
    0x0CFF, 0x0DC3, 0x0E91, 0x0F6F, 0x1056, 0x114E, 0x1259, 0x136C,
    0x149F, 0x15D9, 0x1726, 0x1888, 0x19FD, 0x1B86, 0x1D21, 0x1EDE,
    0x20AB, 0x229C, 0x24B3, 0x26D7, 0x293F, 0x2BB2, 0x2E4C, 0x3110,
    0x33FB, 0x370D, 0x3A43, 0x3DDF, 0x4157, 0x4538, 0x4998, 0x4DAE,
    0x5240, 0x5764, 0x5C9A, 0x61C8, 0x6793, 0x6E19, 0x7485, 0x7BBD,
]

# Intro sound effects (resource id -> the freq byte the intro plays it at, from
# the op_sound trace; first observed freq per id). Used only to pick a WAV rate.
INTRO_SFX = {
    0x03: 21, 0x05: 18, 0x08: 18, 0x09: 18, 0x0A: 18, 0x0B: 18, 0x0C: 18,
    0x0D: 18, 0x0E: 18, 0x0F: 18, 0x2C: 18, 0x33: 18, 0x34: 18, 0x36: 18,
    0x3C: 18, 0x3F: 18, 0x40: 18, 0x41: 18,
}
INTRO_MUSIC = 0x07


def be16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def sound_pcm(data):
    """(pcm_signed_bytes, len_words, loop_words) from an AW sound resource."""
    ln = be16(data, 0); loop = be16(data, 2)
    body = data[8:8 + (ln + loop) * 2]
    return body, ln, loop


def write_wav(path, pcm_signed, rate):
    """Write 8-bit signed PCM as an 8-bit unsigned mono WAV."""
    u = bytes((s + 128) & 0xFF for s in pcm_signed)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(1); w.setframerate(int(rate))
        w.writeframes(u)


def main():
    os.makedirs(OUT, exist_ok=True)
    mem = aw_pack.read_memlist()
    byid = {m.idx: m for m in mem}
    log = []

    def out(s):
        print(s); log.append(s)

    out(f"Another World INTRO audio  (source: {os.path.abspath(aw_pack.PC_DIR)})")
    out("=" * 64)

    # --- sound effects ---
    out(f"\nSOUND EFFECTS ({len(INTRO_SFX)} used by the intro):")
    for rid in sorted(INTRO_SFX):
        m = byid[rid]
        data, ok = aw_pack.load_resource(m)
        if len(data) < 8:
            out(f"  0x{rid:02X}  (empty placeholder, size 0 - skipped)")
            continue
        pcm, ln, loop = sound_pcm(data)
        rate = FREQ_TABLE[INTRO_SFX[rid]]
        p = os.path.join(OUT, f"sfx_{rid:02x}.wav")
        write_wav(p, pcm, rate)
        open(os.path.join(OUT, f"sfx_{rid:02x}.bin"), "wb").write(data)
        out(f"  0x{rid:02X}  {len(pcm):6} samples  loop={'yes' if loop else 'no ':>3}"
            f"  {rate:5} Hz  -> {os.path.basename(p)}")

    # --- music module + its instrument samples ---
    m = byid[INTRO_MUSIC]
    data, ok = aw_pack.load_resource(m)
    open(os.path.join(OUT, "music07.bin"), "wb").write(data)
    delay = be16(data, 0); numord = be16(data, 0x3E)
    order = list(data[0x40:0x40 + numord])
    out(f"\nMUSIC  resource #{INTRO_MUSIC}  ({len(data)} bytes)")
    out(f"  default delay (tempo) = {delay}")
    out(f"  numOrder = {numord}")
    out(f"  order table = {order}")
    out(f"  instruments (sample resId, volume):")
    for i in range(15):
        rid = be16(data, 2 + i * 4); vol = be16(data, 2 + i * 4 + 2)
        if rid == 0:
            continue
        s = byid[rid]; sdata, _ = aw_pack.load_resource(s)
        pcm, ln, loop = sound_pcm(sdata)
        # instruments have no op_sound freq; dump at a neutral 11025 Hz for audition
        p = os.path.join(OUT, f"music07_inst{i:02d}_res{rid:02x}.wav")
        write_wav(p, pcm, 11025)
        out(f"    inst{i:2}: res 0x{rid:02X}  vol={vol:2}  {len(pcm):6} samples"
            f"  loop={'yes' if loop else 'no'}  -> {os.path.basename(p)}")

    open(os.path.join(OUT, "report.txt"), "w").write("\n".join(log) + "\n")
    out(f"\nWrote WAV/bin + report.txt to {os.path.abspath(OUT)}")
    out("NOTE: full multi-channel music render (music07 -> one WAV) needs the AW")
    out("      tracker player; instruments are dumped individually above.")


if __name__ == "__main__":
    main()
