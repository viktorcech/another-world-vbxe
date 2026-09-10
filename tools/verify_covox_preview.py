#!/usr/bin/env python3
"""verify_covox_preview.py - build guard for the MENU TEST SOUND and for the
mid-rail parking of the covox channels this player does not drive.

The other two covox guards answer "where does the sample go?" (verify_covox_base)
and "does the probe answer right?" (verify_covox_detect). Neither looks at the
one path a user actually judges the card by: OPTION in the pre-intro menu.
snd_preview does its OWN base patching -- it cannot call cv_set_base, because at
menu time snd_go_covox has not run and must not run -- so nothing else in the
build checks it. A wrong operand there assembles fine and plays silence, or
garbage, on the one card the author does not own.

What this runs, on the real image, with the real baked sample data sitting in
the VRAM window where the loader puts it:

  A. snd_preview -> the byte stream that lands on the DAC is EXACTLY the sample
     data mapped through voltab8's full-volume row + 64. Not "it wrote something
     to the right address": the actual waveform, nibble by nibble.
  B. it parks base+2/base+3 at mid rail BEFORE the first sample, and leaves the
     driven pair at mid rail after the last one.
  C. snd_apply(base) + snd_mute leave all four channels at $80, so the SUMMED
     output idles dead centre, and the covox IRQ still writes base+0/base+1.

Why B is worth a guard. On a 4-channel card the channels sum in pairs into one
output each (LEFT = ch0 + ch3, RIGHT = ch1 + ch2 -- on a PokeyMAX and in
Altirra's Covox device alike), so a channel this player never writes still lands
in the signal. It has to sit on the same $80 the driven pair centres on: LEFT
then centres at $100 of $000-$1FE, dead centre. Parking it at 0 -- which is what
both players used to do -- centres it at $80 instead: a half-scale DC offset
that costs the analogue stage half its headroom on a plain R-2R ladder, i.e. on
exactly the p-covox bases $D500/$D600/$D700. Only a PokeyMAX VOLONLY register
reads 0 as "silent"; a resistor ladder reads it as the bottom rail. And the menu
is the one place that has to park them itself: snd_go_covox runs at snd_init,
i.e. after START, so during the test sound the latches still hold whatever they
powered up with. Every check is therefore replayed with the channels powered up
at $80, $FF and $00.

The covox is modelled from alt-src/Altirra/source/covox.cpp (ATCovoxEmulator:
addr & 3 picks the channel, ColdReset fills all four with $80).

Usage:   python tools/verify_covox_preview.py <xex> <lst> [test_sfx.bin]
         build.ps1 passes the intro; the game has no menu, so it is skipped.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from verify_covox import load_xex                                # noqa: E402
from verify_covox_detect import CPU                              # noqa: E402

BASES = (0xD280, 0xD500, 0xD600, 0xD700)
POWER = (0x80, 0xFF, 0x00)      # what the latches may hold before anyone writes
MID = 0x100                     # two channels at $80, summed = the centre

fails = []
oks = 0


def check(cond, what):
    global oks
    if cond:
        oks += 1
    else:
        fails.append(what)


# --- mads listing -> labels --------------------------------------------------
#   verify_covox.py's loader only sees labels on lines that emit NO bytes (it
#   splits on the tab in front of the source column). Every table this guard
#   needs -- tst_winlo, voltab8's rows -- is a `dta` line, so strip the emitted
#   byte column off the front instead.
LINE = re.compile(r'^\s*\d+\s+([0-9A-F]{4})\b(.*)$')
BYTECOL = re.compile(r'^(?:\s+[0-9A-F]{2})+\s*\+?')


def load_labels(path):
    lab = {}
    for line in open(path, encoding='utf-8', errors='replace'):
        m = LINE.match(line)
        if not m:
            continue
        addr = int(m.group(1), 16)
        src = BYTECOL.sub('', m.group(2)).replace('\t', ' ').strip()
        if not src:
            continue
        if src.startswith('.proc') or src.startswith('.local'):
            p = src.split()
            if len(p) > 1:
                lab.setdefault(p[1], addr)
            continue
        if src[0] in ';.?':
            continue
        name = src.split()[0]
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            lab.setdefault(name, addr)
    return lab


class Covox:
    """One covox at `base`, four channels, on a bus quiet enough for the
    routines under test: RANDOM hands back `rnd` (snd_preview picks the sound
    with it), POT0-7 read $E4 like disconnected paddles, IRQEN reports Timer 1
    pending so the covox IRQ takes its `ours` branch."""

    def __init__(self, base, rnd=0, power=0x80):
        self.base = base
        self.rnd = rnd
        self.ch = [power] * 4
        self.w = []
        self.lr = []

    def read(self, a):
        if a == 0xD20E:
            return 0xFE
        if a == 0xD20A:
            return self.rnd
        if 0xD200 <= a <= 0xD207:
            return 0xE4
        return 0xFF

    def write(self, a, v):
        self.w.append((a, v))
        if self.base <= a <= self.base + 3:
            self.ch[a & 3] = v
            self.lr.append((self.left, self.right))

    @property
    def left(self):
        return self.ch[0] + self.ch[3]

    @property
    def right(self):
        return self.ch[1] + self.ch[2]

    def dac(self):
        return [(a, v) for a, v in self.w if self.base <= a <= self.base + 3]


def run_preview(mem, lab, idx, rnd, power):
    m = bytearray(mem)
    m[lab['set_sel']] = idx + 1
    m[lab['pm_seen']] = 1                      # $D280 only plays when probed
    hw = Covox(BASES[idx], rnd=rnd, power=power)
    CPU(m, hw).run(lab['snd_preview'], (), limit=20000000)
    return hw


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    xex, lst = sys.argv[1], sys.argv[2]
    sfx = (sys.argv[3] if len(sys.argv) > 3 else
           os.path.join(os.path.dirname(HERE), 'out', 'test_sfx.bin'))

    mem, _ = load_xex(xex)
    lab = load_labels(lst)
    print('verify_covox_preview: %s' % os.path.basename(xex))

    if 'snd_preview' not in lab:
        print('  skipped: no snd_preview in this build (the game has no menu)')
        return 0

    for n in ('set_sel', 'pm_seen', 'voltab8', 'snd_apply', 'snd_mute',
              'cv_irq', 'tst_winlo', 'tst_winhi', 'tst_lenlo', 'tst_lenhi'):
        if n not in lab:
            fails.append('label %s not in the listing' % n)
    if fails:
        for f in fails:
            print('  FAIL', f)
        return 1

    def tbl(n):
        return list(mem[lab[n]:lab[n] + 8])

    winlo, winhi = tbl('tst_winlo'), tbl('tst_winhi')
    lenlo, lenhi = tbl('tst_lenlo'), tbl('tst_lenhi')

    # the loader chunk really carries the sounds at the window address
    if os.path.exists(sfx):
        blob = open(sfx, 'rb').read()
        w0 = winhi[0] << 8 | winlo[0]
        check(bytes(mem[w0:w0 + len(blob)]) == blob,
              'the baked sounds are not in the image at $%04X -- the menu would '
              'play whatever else is in that window' % w0)

    # prv_vt as snd_preview builds it for a covox: voltab8's full-volume row
    # lifted by the 64 the mix tail adds for a silent music voice
    prv = [(mem[lab['voltab8'] + 15 * 16 + n] + 64) & 0xFF for n in range(16)]
    check(prv[8] == 0x80,
          'voltab8 row 15 nibble 8 + 64 = $%02X, not the $80 mid rail the whole '
          'mix is centred on' % prv[8])

    # --- A/B: the waveform, every sound, at the base the p-covox users have --
    base = BASES[3]
    for pick in range(8):
        hw = run_preview(mem, lab, 3, pick, 0x80)
        dac = hw.dac()
        check([t for t in dac[:2]] == [(base + 2, 0x80), (base + 3, 0x80)],
              'sound %d: the first DAC writes are %s -- the undriven pair must '
              'be parked at mid rail BEFORE the first sample'
              % (pick, ['$%04X=$%02X' % t for t in dac[:2]]))
        src = winhi[pick] << 8 | winlo[pick]
        n = lenhi[pick] << 8 | lenlo[pick]
        want = []
        for b in mem[src:src + n]:
            want += [prv[b >> 4], prv[b & 15]]
        got = [v for a, v in dac if a == base]
        check(got[:-1] == want,
              'sound %d: the DAC stream is not the sample data -- %d samples, '
              'want %d' % (pick, len(got) - 1, len(want)))
        check(got[-1] == 0x80,
              'sound %d: parked at $%02X after the last sample, want $80'
              % (pick, got[-1]))

    # --- B: every base, every power-on state, steady-state centring ----------
    for idx, base in enumerate(BASES):
        for power in POWER:
            hw = run_preview(mem, lab, idx, 0, power)
            ports = sorted({a for a, _ in hw.dac()})
            check(ports == [base, base + 1, base + 2, base + 3],
                  'base $%04X power $%02X: the preview touched %s'
                  % (base, power, ['$%04X' % a for a in ports]))
            # entries 0..3 are taken while the park pair / the first sample pair
            # is still in flight and the other channel holds its power-on value
            steady = hw.lr[4:]
            worst = max(max(abs(l - MID), abs(r - MID)) for l, r in steady)
            check(worst <= 120,
                  'base $%04X power $%02X: the summed output strays %d from mid '
                  'rail once playing -- an undriven channel is off centre'
                  % (base, power, worst))

    # --- C: the one-way switch leaves the card centred -----------------------
    for idx, base in enumerate(BASES):
        for power in POWER:
            m = bytearray(mem)
            if 'body' in lab:                  # stop at the shared IRQ body
                m[lab['body']], m[lab['body'] + 1] = 0x68, 0x60
            hw = Covox(base, power=power)
            cpu = CPU(m, hw)
            cpu.a, cpu.z, cpu.n = idx + 1, False, False
            cpu.run(lab['snd_apply'])
            cpu.run(lab['snd_mute'])
            check(hw.ch == [0x80] * 4,
                  'base $%04X power $%02X: after snd_apply + snd_mute the '
                  'channels are %s, want all $80'
                  % (base, power, ['$%02X' % c for c in hw.ch]))
            check(hw.left == MID and hw.right == MID,
                  'base $%04X power $%02X: the card idles at $%03X/$%03X, want '
                  '$%03X/$%03X' % (base, power, hw.left, hw.right, MID, MID))
            hw.w.clear()
            cpu.run(lab['cv_irq'])
            got = [a for a, _ in hw.w if not 0xD200 <= a <= 0xD20F]
            check(got == [base, base + 1],
                  'base $%04X power $%02X: the covox IRQ wrote %s, want '
                  '[$%04X, $%04X]'
                  % (base, power, ['$%04X' % a for a in got], base, base + 1))

    if fails:
        for f in fails:
            print('  FAIL', f)
        print('  %d checks passed, %d FAILED' % (oks, len(fails)))
        return 1
    print('  ok: %d checks passed, 0 failed' % oks)
    return 0


if __name__ == '__main__':
    sys.exit(main())
