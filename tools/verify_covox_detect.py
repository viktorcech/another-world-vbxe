#!/usr/bin/env python3
"""verify_covox_detect.py - build guard for snd_detect (both sound players).

verify_covox.py proves the SMC images are slot-clean. This proves the thing
that decides whether those images are ever installed: the probe.

That decision cannot be tested on the machine the build runs on, and getting it
wrong is expensive in BOTH directions:
  * a false negative is silence-by-POKEY -- the covox owner hears the 4-bit mix
    and nothing says why (that is nearly what shipped: the player wrote $D284/
    $D286, the PokeyMAX SAMPLE registers, and made no sound at all);
  * a false positive is worse. Where there is no covox, $D280 is a POKEY mirror
    of AUDF1 -- the Timer 1 divider both players run their IRQ on -- so every
    sample byte would rewrite the tick rate.
So: run the ASSEMBLED snd_detect on a small 6502 against five machines it can
plausibly meet, and check both the verdict and what it left behind.

  1 stock POKEY            -> no covox, AUDF1 back to 15, SKCTL back to $03
  2 PokeyMAX, COVOX mapped -> covox on probe 1, sample DMA switched off
  3 PokeyMAX, COVOX off in RESTRICT -> covox after pm_try switches it on
  4 PokeyMAX without a COVOX in the core -> no covox, RESTRICT never written
  5 write-swallowing card (Altirra's Covox device) -> covox on probe 3

Usage:   python tools/verify_covox_detect.py <xex> <lst>
         build.ps1 passes the intro and the game pair in turn.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from verify_covox import load_xex, load_labels          # noqa: E402

STOP = 0xFFFE                       # sentinel return address for the outer rts


# --- machines ----------------------------------------------------------------
class Pokey:
    """A stock POKEY: 16 registers mirrored over the whole $D2 page."""
    name = 'stock POKEY'
    pokeymax = False

    def __init__(self):
        self.reg = [0] * 16         # write side
        self.poly = 0
        self.log = []

    # -- the parts a subclass reuses when $D28x is just a shadow --------------
    def pokey_write(self, a, v):
        self.reg[a & 15] = v

    def pokey_read(self, a):
        r = a & 15
        if r == 0x0A:               # RANDOM: frozen to $FF while SKCTL b0-1 = 0
            if self.reg[0x0F] & 3 == 0:
                return 0xFF
            self.poly += 1
            return 0x7F if self.poly & 1 else 0xFE
        if r <= 7:                  # POT0-7: disconnected paddles read $E4
            return 0xE4
        return 0xFF                 # unused / write-only regs read $FF

    def write(self, a, v):
        if 0xD200 <= a <= 0xD2FF:
            self.pokey_write(a, v)     # nothing else on the bus in this model

    def read(self, a):
        if 0xD200 <= a <= 0xD2FF:
            return self.pokey_read(a)
        return 0xFF                    # unmapped $Dxxx

    # -- what the checks look at ---------------------------------------------
    @property
    def audf1(self):
        return self.reg[0]

    @property
    def skctl(self):
        return self.reg[0x0F]


class PokeyMAX(Pokey):
    """$D200-$D20F POKEY 1, $D210-$D21F POKEY 2 or the config bank, and
    $D280-$D29F COVOX + SAMPLE -- but only while RESTRICT bit 4 is set. With it
    clear the whole area falls back to being a shadow of POKEY 1, which is
    exactly the case pm_try exists for."""
    pokeymax = True

    def __init__(self, covox_in_core=True, area_on=True):
        Pokey.__init__(self)
        self.name = 'PokeyMAX (covox %s, area %s)' % (
            'yes' if covox_in_core else 'no', 'on' if area_on else 'off')
        self.cap = 0x32 if covox_in_core else 0x02   # bit4 COVOX, bit5 SAMPLE
        self.restrict = 31 if area_on else 15        # bit4 = COVOX/SAMPLE area
        self.cfg = False                             # config banked in?
        self.covox = [0, 0, 0, 0]
        # $D284-$D29F, pre-loaded with junk so the checks can tell "written 0"
        # from "never touched": SAMDMA starts with all four channels running,
        # the way a previous program that used the sample player would leave it
        self.sample = [0xEE] * 0x1C
        self.sample[0x290 - 0x284] = 0x0F
        self.restrict_writes = 0
        self.hit = set()                             # every $D28x address written

    def area_live(self):
        return bool(self.restrict & 0x10)

    def write(self, a, v):
        if a == 0xD20C or a == 0xD21C:
            self.cfg = (v == 0x3F)
            return
        if self.cfg and 0xD210 <= a <= 0xD21F:
            if a == 0xD217:
                self.restrict = v & 0x1F
                self.restrict_writes += 1
            return
        if 0xD280 <= a <= 0xD2FF and self.area_live():
            a = 0xD280 + (a - 0xD280) % 0x20         # $D2A0+ shadows the block
            self.hit.add(a)
            if a <= 0xD283:
                self.covox[a - 0xD280] = v
            else:
                self.sample[a - 0xD284] = v
            return
        self.pokey_write(a, v)

    def read(self, a):
        if a == 0xD20C or a == 0xD21C:
            return 1                                 # ID
        if self.cfg and 0xD210 <= a <= 0xD21F:
            return {0xD211: self.cap, 0xD217: self.restrict}.get(a, 0)
        if 0xD280 <= a <= 0xD2FF and self.area_live():
            a = 0xD280 + (a - 0xD280) % 0x20
            return self.covox[a - 0xD280] if a <= 0xD283 else self.sample[a - 0xD284]
        return self.pokey_read(a)


class Covox(Pokey):
    """Altirra's Covox device, modelled from covox.cpp + uiconfdevcovox.cpp:

      * the six base/size pairs the config dialog offers, 1 or 4 channels;
      * StaticWriteControl returns !mbPassWrites, and SetAddressRange is called
        with passWrites = (base & 0xFF00) != 0xD200 -- so ONLY a card at $D280
        takes the write off the bus. Everywhere else POKEY (or whatever else
        lives there) still sees it;
      * StaticReadControl returns -1 unconditionally: a covox is write-only,
        reads always fall through to whatever else answers;
      * 4-channel: addr & 3 picks the channel, 0 and 3 sum into LEFT, 1 and 2
        into RIGHT. 1-channel: every write in range sets all four.
    """
    ALTIRRA = [(0xD100, 0x100), (0xD280, 0x80), (0xD500, 0x100),
               (0xD600, 0x40), (0xD600, 0x100), (0xD700, 0x100)]

    def __init__(self, base, size, channels=4):
        Pokey.__init__(self)
        self.lo, self.hi, self.ch = base, base + size - 1, channels
        self.pass_writes = (base & 0xFF00) != 0xD200
        self.name = 'covox $%04X-%04X %s' % (
            self.lo, self.hi, '4ch' if channels == 4 else 'mono')
        self.covox = [0, 0, 0, 0]

    def write(self, a, v):
        if self.lo <= a <= self.hi:
            if self.ch == 4:
                self.covox[a & 3] = v
            else:
                self.covox = [v] * 4
            if not self.pass_writes:
                return                  # POKEY never sees it -- the observable
        Pokey.write(self, a, v)

    @property
    def left(self):
        return self.covox[0] + self.covox[3]

    @property
    def right(self):
        return self.covox[1] + self.covox[2]


# --- just enough 6502 --------------------------------------------------------
class CPU:
    def __init__(self, mem, hw):
        self.m, self.hw = mem, hw
        self.a = self.x = self.y = 0
        self.hits = set()
        self.sp = 0xFF
        self.n = self.z = self.c = False
        self.stack = []

    def rd(self, a):
        return self.hw.read(a) if 0xD000 <= a <= 0xD7FF else self.m[a]

    def wr(self, a, v):
        if 0xD000 <= a <= 0xD7FF:
            self.hw.write(a, v)
        else:
            self.m[a] = v

    def nz(self, v):
        self.z, self.n = v == 0, bool(v & 0x80)
        return v

    def run(self, pc, watch=(), limit=200000):
        """Execute until the outer rts. Returns the set of watched addresses
        the run passed through -- watching rather than stopping means the covox
        switch, the cvpatch copy loop and the muting all really execute."""
        self.stack = [STOP]
        self.hits = set()
        for _ in range(limit):
            if pc == STOP:
                return self.hits
            if pc in watch:
                self.hits.add(pc)
            op = self.m[pc]
            ab = self.m[pc + 1] | (self.m[pc + 2] << 8)
            im = self.m[pc + 1]
            rel = pc + 2 + (im - 256 if im > 127 else im)
            if op == 0xA9:   self.a = self.nz(im);                    pc += 2
            elif op == 0xAD: self.a = self.nz(self.rd(ab));           pc += 3
            elif op == 0x8D: self.wr(ab, self.a);                     pc += 3
            elif op == 0xA2: self.x = self.nz(im);                    pc += 2
            elif op == 0x8E: self.wr(ab, self.x);                     pc += 3
            elif op == 0xA0: self.y = self.nz(im);                    pc += 2
            elif op == 0xC8: self.y = self.nz((self.y + 1) & 0xFF);   pc += 1
            elif op == 0xE8: self.x = self.nz((self.x + 1) & 0xFF);   pc += 1
            elif op == 0xB9: self.a = self.nz(self.rd((ab + self.y) & 0xFFFF)); pc += 3
            elif op == 0xBD: self.a = self.nz(self.rd((ab + self.x) & 0xFFFF)); pc += 3
            elif op == 0x9D: self.wr((ab + self.x) & 0xFFFF, self.a); pc += 3
            elif op in (0xE0, 0xEC):
                v = im if op == 0xE0 else self.rd(ab)
                self.c = self.x >= v
                self.nz((self.x - v) & 0xFF)
                pc += 2 if op == 0xE0 else 3
            elif op == 0x29: self.a = self.nz(self.a & im);           pc += 2
            elif op == 0x2D: self.a = self.nz(self.a & self.rd(ab));  pc += 3
            elif op == 0x09: self.a = self.nz(self.a | im);           pc += 2
            elif op == 0x2C: self.nz(self.a & self.rd(ab));           pc += 3
            elif op in (0xC9, 0xCD):
                v = im if op == 0xC9 else self.rd(ab)
                d = (self.a - v) & 0xFF
                self.c = self.a >= v
                self.nz(d)
                pc += 2 if op == 0xC9 else 3
            elif op == 0x48: self.stack.append(self.a);               pc += 1
            elif op == 0x68: self.a = self.nz(self.stack.pop());      pc += 1
            elif op == 0x38: self.c = True;                           pc += 1
            elif op == 0x18: self.c = False;                          pc += 1
            elif op == 0x20: self.stack.append(pc + 3); pc = ab
            elif op == 0x60: pc = self.stack.pop()
            elif op == 0x4C: pc = ab
            elif op == 0xD0: pc = rel if not self.z else pc + 2
            elif op == 0xF0: pc = rel if self.z else pc + 2
            elif op == 0x30: pc = rel if self.n else pc + 2
            elif op == 0x10: pc = rel if not self.n else pc + 2
            elif op == 0xB0: pc = rel if self.c else pc + 2
            elif op == 0x90: pc = rel if not self.c else pc + 2
            else:
                raise SystemExit('verify_covox_detect: opcode $%02X at $%04X is '
                                 'not modelled -- extend CPU.run' % (op, pc))
        raise SystemExit('verify_covox_detect: snd_detect did not terminate')


# --- checks ------------------------------------------------------------------
fails, oks = [], 0


def check(cond, what):
    global oks
    if cond:
        oks += 1
    else:
        fails.append(what)


def detect(mem, lab, hw):
    """Run snd_detect to completion -- including snd_go_covox and its cvpatch
    copy loop when it takes that branch. -> (went_covox, cpu)"""
    cpu = CPU(bytearray(mem), hw)
    hits = cpu.run(lab['snd_detect'], {lab['snd_go_covox']})
    return lab['snd_go_covox'] in hits, cpu


def check_ports(hw):
    """The player must have talked to the COVOX registers and to nothing else in
    the block. This is the check that fails when the ports drift back into the
    SAMPLE range -- $D284/$D286 read back just as happily as $D280 does, they
    just do not make a sound, so a read-back probe alone cannot notice.
    Written as "which addresses were touched" rather than "what is in them",
    because the two players leave the DAC on different values: the intro parks
    it later, from snd_mute, the game does it in snd_go_covox itself."""
    check(hw.hit & {0xD280, 0xD281},
          '%s: the COVOX registers were never written -- touched %s. Wrong port?'
          % (hw.name, sorted('$%04X' % a for a in hw.hit)))
    stray = hw.hit & {0xD284, 0xD285, 0xD286, 0xD287}
    check(not stray,
          '%s: wrote %s -- that is the SAMPLE block (RAMADDR/RAMDATA), it moves a '
          'block-RAM pointer and makes no sound'
          % (hw.name, sorted('$%04X' % a for a in stray)))
    check(hw.sample[0x290 - 0x284] == 0,
          '%s: sample DMA left running (SAMDMA = $%02X) -- DMA writes the very '
          'registers this player writes'
          % (hw.name, hw.sample[0x290 - 0x284]))


def run_all(mem, lab):
    """Every machine, against one memory image. -> the list of failures.
    Split out of main() so a mutation harness can point it at a deliberately
    broken image and confirm these checks still have teeth."""
    global fails, oks
    fails, oks = [], 0
    for n in ('snd_detect', 'snd_go_covox', 'cv_probe', 'pm_try',
              'cv_unrestrict', 'cv_rest'):
        if n not in lab:
            fails.append('label %s not in the listing' % n)
    if fails:
        return fails

    # 1 -- a plain machine must be left EXACTLY as it was found
    hw = Pokey()
    covox, _ = detect(mem, lab, hw)
    check(not covox, '%s: reported a covox' % hw.name)
    check(hw.audf1 == 15, '%s: AUDF1 left at $%02X, want $0F -- the probe wrote '
                          'the timer divider and did not put it back' % (hw.name, hw.audf1))
    check(hw.skctl == 0x03, '%s: SKCTL left at $%02X, want $03 (keyboard scan)'
                            % (hw.name, hw.skctl))

    # 2 -- the shipping case: PokeyMAX with the covox block mapped
    hw = PokeyMAX()
    covox, cpu = detect(mem, lab, hw)
    check(covox, '%s: not detected' % hw.name)
    check_ports(hw)
    check(hw.restrict_writes == 0,
          '%s: RESTRICT written although the area was already on' % hw.name)
    check(hw.audf1 == 15, '%s: AUDF1 = $%02X, want $0F' % (hw.name, hw.audf1))

    # 3 -- covox present but switched off in RESTRICT: pm_try must turn it on
    hw = PokeyMAX(area_on=False)
    covox, cpu = detect(mem, lab, hw)
    check(covox, '%s: not detected -- pm_try did not enable the area' % hw.name)
    check_ports(hw)
    check(hw.restrict & 0x10, '%s: RESTRICT bit4 still clear' % hw.name)
    check(not hw.cfg, '%s: config bank left mapped over $D210-$D21F' % hw.name)
    check(cpu.m[lab['cv_rest']] == 15,
          '%s: saved RESTRICT = $%02X, want $0F' % (hw.name, cpu.m[lab['cv_rest']]))
    #    ... and snd_stop must hand the machine back the way it was
    cpu.run(lab['cv_unrestrict'])
    check(hw.restrict == 15, '%s: cv_unrestrict left RESTRICT = $%02X, want $0F'
                             % (hw.name, hw.restrict))
    check(not hw.cfg, '%s: cv_unrestrict left the config bank mapped' % hw.name)
    check(cpu.m[lab['cv_rest']] == 0xFF,
          '%s: cv_unrestrict did not disarm cv_rest' % hw.name)
    cpu.run(lab['cv_unrestrict'])
    check(hw.restrict == 15, '%s: a second cv_unrestrict changed RESTRICT again'
                             % hw.name)

    # 4 -- a PokeyMAX whose core has no covox at all
    hw = PokeyMAX(covox_in_core=False, area_on=False)
    covox, _ = detect(mem, lab, hw)
    check(not covox, '%s: reported a covox' % hw.name)
    check(hw.restrict_writes == 0, '%s: RESTRICT written anyway' % hw.name)
    check(not hw.cfg, '%s: config bank left mapped' % hw.name)
    check(hw.audf1 == 15, '%s: AUDF1 = $%02X, want $0F' % (hw.name, hw.audf1))

    # 5 -- every covox Altirra can be configured with, both channel counts.
    #      Only a card at $D280 is FINDABLE, and not by luck: it is the only
    #      base where the card takes a write POKEY would otherwise have taken,
    #      which is the one and only thing a write-only DAC ever makes
    #      observable. The rest must fall back to POKEY and leave no mess.
    for base, size in Covox.ALTIRRA:
        for ch in (4, 1):
            hw = Covox(base, size, ch)
            covox, cpu = detect(mem, lab, hw)
            findable = base == 0xD280
            check(covox == findable,
                  '%s: %s' % (hw.name, 'not detected -- it inhibits POKEY, so the '
                              'swallow probe must see it' if findable else
                              'reported a covox, but nothing about this card is '
                              'observable -- that is a FALSE POSITIVE and $D280 '
                              'is AUDF1 on this machine'))
            check(hw.audf1 == 15, '%s: AUDF1 left at $%02X, want $0F'
                                  % (hw.name, hw.audf1))
            check(hw.skctl == 0x03, '%s: SKCTL left at $%02X, want $03'
                                    % (hw.name, hw.skctl))
            if not covox:
                continue
            # probe 3 has to write $D28F -- that is the SKCTL mirror -- and
            # $D28F & 3 = 3, so on a 4-channel card it lands in ch4 (LEFT).
            # snd_go_covox must scrub that, or the left channel carries a
            # permanent +$80 and clips the moment the samples get loud.
            #  (a mono card cannot be unbalanced -- every write hits all four)
            if ch == 4:
                check(hw.covox[2] == 0 and hw.covox[3] == 0,
                      '%s: undriven channels left at $%02X/$%02X, want 0/0 -- '
                      'probe 3 left a DC offset in one of them'
                      % (hw.name, hw.covox[2], hw.covox[3]))
            if 'snd_mute' in lab:
                cpu.run(lab['snd_mute'])     # exercises the SMC'd mute as well
                #  a mono card sums to 2x the written value on both sides
                #  (covox.cpp WriteMono), so compare the sides, not a constant
                check(hw.left == hw.right,
                      '%s: after snd_mute L/R = $%02X/$%02X -- the two sides must '
                      'carry the same DC or the image is off centre'
                      % (hw.name, hw.left, hw.right))
                check(hw.covox[0] == 0x80,
                      '%s: after snd_mute the DAC sits at $%02X, want $80 (mid '
                      'rail -- anything else is a click)' % (hw.name, hw.covox[0]))

    return fails


def main():
    xex = sys.argv[1] if len(sys.argv) > 1 else 'out/_cv.xex'
    lst = sys.argv[2] if len(sys.argv) > 2 else 'out/_cv.lst'
    mem, _ = load_xex(xex)
    run_all(mem, load_labels(lst))
    return report()


def report():
    for f in fails:
        print('  FAIL %s' % f)
    print('verify_covox_detect: %d ok, %d failed' % (oks, len(fails)))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
