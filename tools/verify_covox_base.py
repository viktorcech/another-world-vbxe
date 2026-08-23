#!/usr/bin/env python3
"""verify_covox_base.py - build guard for the SELECTABLE covox base.

The intro's menu (src/aw_settings.asm) lets the user say where their covox is:
$D280 (PokeyMAX), $D500, $D600 or $D700 (p-covox jumpers). cv_set_base then
rewrites the operand of every store the player aims at the DAC -- there is no
runtime base variable, because the DAC write sits in the IRQ head at a fixed
offset from entry and the prologue has no index register to spare.

That makes cvport, the table of operand ADDRESSES, load-bearing and invisible:
a wrong or missing entry assembles fine, runs fine on the machine the author
has, and drops half the samples on the address the author does not have. So:
run the assembled cv_set_base on a 6502 for every base, then run the code it
patched and check WHERE the sample actually lands.

Checks, driven by the cvport table in the binary itself (so it covers both
players -- src/aw_sound.asm and src_game/game_sound.asm):
  1. every cvport entry points at the operand of a real `sta abs`, and that
     store targets $D280+channel as assembled
  2. no entry is listed twice, and every channel 0..3 story is consistent
     (channels 0/3 = left, 1/2 = right, as in Altirra's Covox device)
  3. for each of the four bases: cv_set_base rewrites every entry to base+ch
     and touches nothing else
  4. for each base: after cv_set_base + snd_go_covox the covox IRQ prologue
     really writes the pending sample to base+0 and base+1 -- executed, not
     assumed
  5. for each base: the silence path (the intro's snd_mute, the game's copied
     cvm slot) parks the DAC at base too, not at the assembled $D280
  6. snd_apply routes: $00 = POKEY installs nothing at all, 1..4 install that
     base, $FF falls through to the old $D280 probe
  7. no `sta $D280..$D283` is left anywhere outside cv_probe and the table --
     i.e. nobody added a DAC write and forgot to list it

Usage:   python tools/verify_covox_base.py <xex> <lst>
         build.ps1 passes the intro and the game pair in turn.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from verify_covox import load_xex, load_labels                    # noqa: E402
from verify_covox_detect import CPU, STOP, PokeyMAX                         # noqa: E402

BASES = (0xD280, 0xD500, 0xD600, 0xD700)
CFG_MAGIC, CFG_MODE, CFG_OK = 0x04FE, 0x04FF, 0xA7   # intro -> game handoff cell
VIMIRQ = 0x0216                                      # OS immediate-IRQ vector

fails, oks = [], 0


def check(cond, what):
    global oks
    if cond:
        oks += 1
    else:
        fails.append(what)


class Bus:
    """Just enough hardware: Timer 1 reads as pending (so the covox IRQ takes
    its `ours` branch), everything else reads $FF, and every write is logged --
    the log IS the answer to "where did the sample go?"."""
    name = 'bus'

    def __init__(self):
        self.w = []

    def read(self, a):
        return 0xFE if a == 0xD20E else 0xFF     # IRQEN bit0 = 0 -> ours

    def write(self, a, v):
        self.w.append((a, v))

    def stores(self):
        return [a for a, _ in self.w if not 0xD200 <= a <= 0xD20F]


def read_cvport(mem, addr):
    """[(operand address, channel)] -- the table the player patches from."""
    out = []
    while True:
        lo, hi, ch = mem[addr], mem[addr + 1], mem[addr + 2]
        if hi == 0:
            return out
        out.append((lo | (hi << 8), ch))
        addr += 3
        if len(out) > 32:
            raise SystemExit('verify_covox_base: cvport has no terminator')


def operand(mem, a):
    return mem[a] | (mem[a + 1] << 8)


def setacc(cpu, a):
    """A = a with the flags an `lda` would leave -- snd_apply branches on them."""
    cpu.a = a
    cpu.z = a == 0
    cpu.n = bool(a & 0x80)


def stub(m, lab):
    """Stop a run at the shared IRQ body with `pla / rts`. Not a bare rts: the
    covox prologue enters after a `pha` that the real tail balances with its own
    `pla / rti`, and the 6502 model keeps one stack for both."""
    m[lab['body']] = 0x68
    m[lab['body'] + 1] = 0x60


def run(mem, lab, entry, a=0, stub_body=True):
    """Execute one routine on a fresh copy of the image. -> (cpu, bus, mem)."""
    m = bytearray(mem)
    if stub_body and 'body' in lab:
        stub(m, lab)
    bus = Bus()
    cpu = CPU(m, bus)
    setacc(cpu, a)
    cpu.run(lab[entry])
    return cpu, bus, m


def run_all(mem, lab):
    global fails, oks
    fails, oks = [], 0
    for n in ('cv_set_base', 'cvport', 'cv_blo', 'cv_bhi', 'snd_apply',
              'snd_go_covox', 'cv_irq', 'cv_probe'):
        if n not in lab:
            fails.append('label %s not in the listing' % n)
    if fails:
        return fails

    entries = read_cvport(mem, lab['cvport'])
    check(len(entries) >= 4,
          'cvport lists only %d stores -- the IRQ pair alone is 2' % len(entries))

    # 1+2 -- the table describes real stores, once each
    seen = set()
    for a, ch in entries:
        check(mem[a - 1] == 0x8D,
              'cvport entry $%04X: the byte before it is $%02X, not $8D (sta abs)'
              % (a, mem[a - 1]))
        check(operand(mem, a) == 0xD280 + ch,
              'cvport entry $%04X says channel %d but the assembled store targets '
              '$%04X, not $%04X' % (a, ch, operand(mem, a), 0xD280 + ch))
        check(ch < 4, 'cvport entry $%04X: channel %d is not 0..3' % (a, ch))
        check(a not in seen, 'cvport lists $%04X twice' % a)
        seen.add(a)

    # 7 -- nothing writes the DAC from outside the table
    probe_lo, probe_hi = lab['cv_probe'], lab['cv_probe'] + 0x20
    for a in range(0x0200, 0xC000 - 3):
        if mem[a] == 0x8D and mem[a + 2] == 0xD2 and mem[a + 1] in (0x80, 0x81, 0x82, 0x83):
            if a + 1 in seen or probe_lo <= a <= probe_hi:
                continue
            fails.append('$%04X: sta $%04X is a DAC write that cvport does not '
                         'list -- it would stay at $D280 on every other card'
                         % (a, operand(mem, a + 1)))

    for idx, base in enumerate(BASES):
        # 3 -- the patcher moves every listed store and nothing else
        _, _, m = run(mem, lab, 'cv_set_base', a=idx)
        for a, ch in entries:
            check(operand(m, a) == base + ch,
                  'base $%04X: cvport entry $%04X (ch %d) ended up at $%04X'
                  % (base, a, ch, operand(m, a)))
        ref = bytearray(mem)
        stub(ref, lab)
        own = range(lab['cv_set_base'], lab['cv_blo'])   # its own SMC operands
        diff = [a for a in range(0x0200, 0xC000)
                if m[a] != ref[a] and a not in seen and a - 1 not in seen
                and a not in own]
        check(not diff,
              'base $%04X: cv_set_base also wrote %s -- it must touch nothing but '
              'the listed operands' % (base, ['$%04X' % a for a in diff[:6]]))

        # 4 -- the switched-over IRQ head really writes THIS base
        m2 = bytearray(m)
        cpu = CPU(m2, Bus())
        cpu.run(lab['snd_go_covox'])
        _, bus, m3 = (None, None, m2)
        bus = Bus()
        if 'body' in lab:
            stub(m3, lab)
        cpu = CPU(m3, bus)
        cpu.run(lab['cv_irq'])
        check(bus.stores() == [base, base + 1],
              'base $%04X: the covox IRQ wrote %s, want [$%04X, $%04X] -- the '
              'sample is going to the wrong card' %
              (base, ['$%04X' % a for a in bus.stores()], base, base + 1))

        # 5 -- and so does silence
        if 'mm5' in lab:                          # intro: snd_mute parks the DAC
            bus = Bus()
            cpu = CPU(bytearray(m3), bus)
            cpu.run(lab['snd_mute'])
            check(bus.stores() == [base, base + 1],
                  'base $%04X: snd_mute parked %s, want [$%04X, $%04X]' %
                  (base, ['$%04X' % a for a in bus.stores()], base, base + 1))
        if 'cvm' in lab:                          # game: the copied silence slot
            got = (operand(m3, lab['cvm'] + 3), operand(m3, lab['cvm'] + 6))
            check(got == (base, base + 1),
                  'base $%04X: the copied silence slot parks ($%04X, $%04X)' %
                  (base, got[0], got[1]))

    # A -ForceCovox test disk (build.ps1 -ForceCovox) has no POKEY path at all --
    # snd_apply is assembled but never called -- so the end-to-end expectations
    # below do not apply to it. Detect it by that missing call, not by a flag.
    forced = not any(mem[a] in (0x20, 0x4C) and operand(mem, a + 1) == lab['snd_apply']
                     for a in range(0x0200, 0xC000 - 3))
    if forced:
        print('  note: -ForceCovox build -- the POKEY/menu end-to-end checks do '
              'not apply and are skipped')

    # 8 -- end to end: hand snd_init the answer the menu would have left and
    #      check what it installs, INCLUDING which IRQ prologue it hooks. This is
    #      the one that catches "the mode was chosen after snd_init hooked the
    #      IRQ" -- the DAC would then never be written and the machine is silent.
    if not forced and 'snd_init' in lab and 'snd_irq' in lab:
        for mode in (0, 1, 2, 3, 4, 0xFF):
            m = bytearray(mem)
            stub(m, lab)
            if 'snd_getmode' in lab:              # game: it arrives in page 4
                m[CFG_MAGIC] = 0x00 if mode == 0xFF else CFG_OK
                m[CFG_MODE] = 0 if mode == 0xFF else mode
            else:                                 # intro: the menu wrote snd_mode
                m[lab['snd_mode']] = mode
            cpu = CPU(m, Bus())
            hits = cpu.run(lab['snd_init'], {lab['snd_go_covox']})
            covox = mode not in (0, 0xFF)         # $FF probes, and this bus has no card
            want = lab['cv_irq'] if covox else lab['snd_irq']
            got = m[VIMIRQ] | (m[VIMIRQ + 1] << 8)
            check(got == want,
                  'mode %s: snd_init hooked $%04X, want $%04X (%s prologue)' %
                  (mode, got, want, 'covox' if covox else 'POKEY'))
            check((lab['snd_go_covox'] in hits) == covox,
                  'mode %s: snd_go_covox %s run' %
                  (mode, 'was' if not covox else 'was NOT'))
            if covox:
                base = BASES[mode - 1]
                check(all(operand(m, a) == base + ch for a, ch in entries),
                      'mode %d: snd_init did not put the stores on $%04X'
                      % (mode, base))

    # 10 -- on a machine that DOES have a covox at $D280 (a PokeyMAX, the common
    #       case), the menu still decides: an explicit POKEY must not be probed
    #       away, and an explicit $D700 must not be pulled back to $D280.
    if not forced and 'snd_init' in lab and 'snd_irq' in lab:
        for mode, covox, base in ((0x00, False, None), (0xFF, True, 0xD280),
                                  (0x04, True, 0xD700)):
            m = bytearray(mem)
            stub(m, lab)
            if 'snd_getmode' in lab:
                m[CFG_MAGIC] = 0x00 if mode == 0xFF else CFG_OK
                m[CFG_MODE] = 0 if mode == 0xFF else mode
            else:
                m[lab['snd_mode']] = mode
            cpu = CPU(m, PokeyMAX())
            hits = cpu.run(lab['snd_init'], {lab['snd_go_covox']})
            check((lab['snd_go_covox'] in hits) == covox,
                  'PokeyMAX + answer %s: covox %s installed' %
                  (mode, 'was NOT' if covox else 'WAS'))
            want = lab['cv_irq'] if covox else lab['snd_irq']
            got = m[VIMIRQ] | (m[VIMIRQ + 1] << 8)
            check(got == want,
                  'PokeyMAX + answer %s: hooked $%04X, want $%04X' % (mode, got, want))
            if base:
                check(all(operand(m, a) == base + ch for a, ch in entries),
                      'PokeyMAX + answer %s: the stores did not land on $%04X'
                      % (mode, base))

    # 9 -- the two halves agree on where the answer is parked
    if 'snd_set_mode' in lab:
        m = bytearray(mem)
        cpu = CPU(m, Bus())
        setacc(cpu, 3)
        cpu.run(lab['snd_set_mode'])
        check(m[lab['snd_mode']] == 3,
              'snd_set_mode(3) left snd_mode = $%02X' % m[lab['snd_mode']])
        check(m[CFG_MODE] == 3 and m[CFG_MAGIC] == CFG_OK,
              'snd_set_mode(3) left the $%04X handoff cell as ($%02X, $%02X) -- the '
              'GAME reads it from there and would fall back to the probe'
              % (CFG_MAGIC, m[CFG_MAGIC], m[CFG_MODE]))

    # 6 -- snd_apply's routing
    _, _, m = run(mem, lab, 'snd_apply', a=0x00)
    check(all(operand(m, a) == 0xD280 + ch for a, ch in entries),
          'snd_apply(0) = POKEY moved the covox stores -- it must install nothing')
    check(m[lab['cv_irq']] == mem[lab['cv_irq']],
          'snd_apply(0) = POKEY patched code anyway')
    for idx, base in enumerate(BASES):
        _, _, m = run(mem, lab, 'snd_apply', a=idx + 1)
        check(all(operand(m, a) == base + ch for a, ch in entries),
              'snd_apply(%d) did not install base $%04X' % (idx + 1, base))
    if 'snd_detect' in lab:
        cpu = CPU(bytearray(mem), Bus())
        setacc(cpu, 0xFF)
        hits = cpu.run(lab['snd_apply'], {lab['snd_detect']})
        check(lab['snd_detect'] in hits,
              'snd_apply($FF) did not fall through to the $D280 probe -- a build '
              'with no menu answer would come up silent')
    return fails


def report():
    for f in fails:
        print('  FAIL ' + f)
    print('  %s: %d checks passed, %d failed'
          % ('ok' if not fails else 'FAILED', oks, len(fails)))


def main():
    xex = sys.argv[1] if len(sys.argv) > 1 else 'awintro.xex'
    lst = sys.argv[2] if len(sys.argv) > 2 else 'out/awintro.lst'
    mem, _ = load_xex(xex)
    lab = load_labels(lst)
    if 'cv_set_base' not in lab:
        print('  verify_covox_base: no cv_set_base in %s -- nothing to check' % lst)
        return 0
    run_all(mem, lab)
    print('verify_covox_base: %s' % os.path.basename(xex))
    report()
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
