#!/usr/bin/env python3
"""skill_tips -- the two skills (.claude/skills/6502-idioms, vbxe-blitter) looked for
in the WHOLE assembled build -- every .asm of the game AND the intro, not the hot
procs. Port of the doom port's tools/dev/drac_tips.py to NMOS 6502: reads the MADS
listings (out/awgame.lst, out/awintro.lst) through tools/_lst6502.py, finds the source
line by number AND text, and proves the mechanical rewrites from the listing.

MECHANICAL -- proved from the listing, `--apply` rewrites them:
  KNOWNC  bcc L / sec  and  bcs L / clc: the flag is already that; the carry a
          not-taken branch proves survives loads, stores, transfers, inc/dec,
          inx.., pha/pla (no entry point on the way)                 (-1 B, -2)
  CKNOWN  clc / adc #v with C proven 1 -> adc #v-1 ; sec / sbc #v with C
          proven 0 -> sbc #v-1 (literal v only)                      (-1 B, -2)
  CLCASL  and #m / asl.. / clc: the shifts pushed out zeros only      (-1 B, -2)
  BRINV   bxx L / jmp T / L: -> b(not xx) T                          (-3 B, -1..3)
  JMPNXT  jmp L where L is the next instruction                      (-3 B, -3)
  JSRRTS  jsr X / rts -> jmp X                                       (-1 B, -9)
  RELOAD  sta M / lda M (stx/ldx, sty/ldy) -- the register still holds it
          and N/Z already read that way (or are rewritten next)      (-2..3 B, -3/4)
  DUPIMM  lda #k when A already holds the literal k (one load, many stores),
          nothing but stores / non-A work since                      (-2 B, -2)
  STXY0   lda #0 / sta M.. with X or Y proven 0 -> stx/sty M.. (A and N/Z
          dead after the stores)                                     (-2 B, -2)
  CMP0    cmp #0 / cpx #0 / cpy #0 straight after an op that set N/Z from
          that register, C dead after                                (-2 B, -2)

REPORT ONLY -- each needs a read by hand:
  MSHIFT  sta M / asl|lsr|rol|ror M: the value was in A, shift it there
  MPAIR   asl M / rol M+1 and lsr M+1 / ror M pairs (multi-byte shift in memory)
  LOOPCP  inx|iny / cpx|cpy #n / branch back: count to zero instead
  LSR5    five or more lsr/asl of A in a row (the other way round with rol/ror)
  SAVEX   txa|tya / pha ... pla / tax|tay: stx/ldx zp (6 vs 11) unless re-entrant
  TINY    jsr to a routine whose body is cheaper than the 12-cycle call
  HWRMW   inc/dec/shift straight on a hardware register ($D000-$D7FF)
  PAGEX   a loop's taken back-branch that crosses a page (+1 every pass)
  BRPAGE  a forward branch that crosses a page (+1 when taken); `skip` = it jumps
          over a rare jsr (the mfetch/pfetch `bne *+5`): taken ~always
  BWAIT   vbxe: a blitter START followed by a BUSY wait on the same straight path
          ("fire early, wait late")
  BCBK    vbxe: constants written into the span BCB ($8100-$8114) on every call
          (template candidates -- write only the fields that change)
  BWIDTH  vbxe: a BCB WIDTH hi byte with bits above bit 0 (width is 9 bits, 1..512)
  MASKSH  and #mask straight before shifts that drop exactly those bits
  CMPSBC  cmp M / bcc out / sec / sbc M -> sec / sbc M / bcc out (A dies on the branch)
  DEC16   a 16-bit decrement done with sec/sbc #1/sbc #0 instead of the branch form
  PHAPLA  pha ... pla around a call in a routine that is not re-entrant (zp is 6 vs 11)
  JMPIND  jmp ($xxFF): the NMOS takes the high byte from $xx00
  MADSMAC a MADS macro command (mwa/mva/adw/inw/...) -- fine cold, never in a hot path
  TABX    an indexed table read inside a loop, from a base in the last quarter of a
          page (a `tab-1` at a page start, or a 64+ entry table that runs past it)
  TEMPRD  drac030: sta M ... lda M (stx/ldx, sty/ldy) with nothing between that
          touches the register or M -- the register still holds it
  CMPSBCG drac030: cmp M / bcc|bcs out / ... / sec / sbc M with the value kept in A
  JMPFLAG drac030: jmp T in branch range while a flag is proven on the straight path
          (a branch on it is 1 byte shorter and relocatable)

A removed instruction is commented out in place (`;` in front of the mnemonic, the
label stays); a changed one is edited. Every touched line is recorded VERBATIM in
out/skill_tips_applied.txt and `--revert` puts it back. A shared file (src/aw_vbxe.asm
is in both builds) is only edited where BOTH listings prove the same edit.

    python tools/skill_tips.py                 report -> out/skill_tips.txt
    python tools/skill_tips.py --apply [--only KNOWNC,RELOAD,...]
    python tools/skill_tips.py --revert
"""
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _lst6502 as LS                                             # noqa: E402

OUTDIR = os.path.join(PROJ, 'out')
APPLIED = os.path.join(OUTDIR, 'skill_tips_applied.txt')
MECH = ('KNOWNC', 'CKNOWN', 'CLCASL', 'BRINV', 'JMPNXT', 'JSRRTS', 'RELOAD', 'DUPIMM',
        'STXY0', 'CMP0')
REPORT = ('MSHIFT', 'MPAIR', 'LOOPCP', 'LSR5', 'SAVEX', 'TINY', 'HWRMW', 'PAGEX', 'BRPAGE',
          'BWAIT', 'BCBK', 'BWIDTH', 'MASKSH', 'CMPSBC', 'DEC16', 'PHAPLA', 'JMPIND',
          'MADSMAC', 'TABX', 'TEMPRD', 'CMPSBCG', 'JMPFLAG')

BRANCH = LS.BRANCH
INVERT = {'bcc': 'bcs', 'bcs': 'bcc', 'beq': 'bne', 'bne': 'beq',
          'bmi': 'bpl', 'bpl': 'bmi', 'bvc': 'bvs', 'bvs': 'bvc'}
NZ_FROM = {'A': {'lda', 'adc', 'sbc', 'and', 'ora', 'eor', 'pla', 'txa', 'tya'},
           'X': {'ldx', 'tax', 'inx', 'dex', 'tsx'},
           'Y': {'ldy', 'tay', 'iny', 'dey'}}
NZ_SETTERS = {'lda', 'ldx', 'ldy', 'adc', 'sbc', 'and', 'ora', 'eor', 'pla', 'txa', 'tya',
              'tax', 'tay', 'tsx', 'inx', 'iny', 'dex', 'dey', 'cmp', 'cpx', 'cpy', 'bit',
              'inc', 'dec', 'asl', 'lsr', 'rol', 'ror', 'plp', 'rti'}
C_READ = {'adc', 'sbc', 'rol', 'ror', 'bcc', 'bcs', 'php'}
C_WRITE = {'clc', 'sec', 'cmp', 'cpx', 'cpy', 'asl', 'lsr', 'plp', 'rti'}
# instructions that leave C alone (the carry a branch proved survives them)
C_KEEP = {'lda', 'ldx', 'ldy', 'sta', 'stx', 'sty', 'tax', 'tay', 'txa', 'tya', 'tsx', 'txs',
          'inc', 'dec', 'inx', 'iny', 'dex', 'dey', 'pha', 'pla', 'and', 'ora', 'eor', 'bit',
          'nop', 'cli', 'sei', 'clv', 'cld'}
A_READ = {'sta', 'pha', 'adc', 'sbc', 'and', 'ora', 'eor', 'cmp', 'bit', 'tax', 'tay'}
A_KILL = {'lda', 'pla', 'txa', 'tya'}
NZ_READ = {'beq', 'bne', 'bmi', 'bpl', 'php'}
FLOW = BRANCH | {'jmp', 'jsr', 'rts', 'rti', 'brk'}
VOLATILE = [(0x12, 0x14), (0xD000, 0xD7FF), (0x4000, 0x8FFF)]   # RTCLOK, I/O, VBXE windows
MARGIN = 8
LITERAL = re.compile(r'#\s*(?:\$[0-9A-Fa-f]+|%[01]+|[0-9]+)\s*$')
ZERO = re.compile(r'#\s*(?:\$0+|%0+|0+)\s*$')
BL_START = 0xD653
BCB_LO, BCB_HI = 0x8100, 0x8114

out = []


def say(s=''):
    out.append(s)
    print(s)


class E:
    """One code line: i = its single instruction (None for a macro line or a line
    that assembled to two instructions), entry = another path can arrive here."""
    __slots__ = ('i', 'entry', 'build', 'k')


def load(build):
    ins = LS.read(build)
    per_line = Counter((i.file, i.line) for i in ins if not i.data)
    seq = []
    for i in ins:
        e = E()
        e.build = build
        e.entry = bool(i.labels)
        if i.data or i.macro or per_line[(i.file, i.line)] != 1:
            e.i = None if not i.data else None
            e.entry = True
            # keep the instruction for flow reasoning, but mark it not editable
            e.i = None
            e.k = i
        else:
            e.i = i
            e.k = i
        seq.append(e)
    # refs of every label + SMC targets (a store into code)
    return seq


def text_ok(i):
    fs = LS._file_lines(i.file)
    return fs is not None and 0 < i.line <= len(fs) and fs[i.line - 1].strip() == i.src.strip()


def run(seq, k, n):
    """seq[k:k+n] if they are n single, editable instructions, physically consecutive,
    with no entry point on any but the first."""
    w = seq[k:k + n]
    if len(w) < n or any(e.i is None for e in w):
        return None
    for a, b in zip(w, w[1:]):
        if b.entry or b.i.addr != a.i.addr + a.i.size:
            return None
    return w


def volatile(i):
    return i.arg is None or any(lo <= i.arg <= hi for lo, hi in VOLATILE)


IX = {}
LABADDR = {}


def dead(seq, k, is_read, is_kill, hops=2):
    """Walking on from seq[k]: is the thing rewritten before it is read? A label
    further down is harmless (another path JOINING does not read what this path
    left behind); a conditional branch, a return or an indirect jump = not proven.
    jsr / jmp with a listed target are followed."""
    for _ in range(40):
        if k is None or k >= len(seq):
            return False
        e = seq[k]
        i = e.k
        if i.data or i.mn is None:
            return False
        if is_read(i):
            return False
        if is_kill(i):
            return True
        if i.mn in ('jsr', 'jmp') and i.mode == 'abs' and hops:
            k, hops = IX.get((e.build, i.arg)), hops - 1
            continue
        if i.mn in FLOW:
            return False
        k += 1
    return False


def c_dead(seq, k):
    return dead(seq, k, lambda i: i.mn in C_READ, lambda i: i.mn in C_WRITE)


def reg_dead(seq, k, r):
    rd = {'A': lambda i: i.mn in A_READ or i.mode == 'acc'
                  or (i.mn in ('sta',)) or (i.mode in ('abx', 'zpx', 'izx') and False),
          'X': lambda i: i.mn in ('stx', 'txa', 'txs', 'cpx', 'inx', 'dex') or i.mode in ('abx', 'zpx', 'izx'),
          'Y': lambda i: i.mn in ('sty', 'tya', 'cpy', 'iny', 'dey') or i.mode in ('aby', 'zpy', 'izy')}[r]
    kill = {'A': lambda i: i.mn in A_KILL, 'X': lambda i: i.mn in ('ldx', 'tax', 'tsx'),
            'Y': lambda i: i.mn in ('ldy', 'tay')}[r]
    return dead(seq, k, rd, kill)


def nz_dead(seq, k):
    return dead(seq, k, lambda i: i.mn in NZ_READ, lambda i: i.mn in NZ_SETTERS)


def smc_targets(seq):
    """code bytes some instruction stores into (self-modified operands/opcodes)."""
    code = set()
    for e in seq:
        i = e.k
        if not i.data:
            for a in range(i.addr, i.addr + i.size):
                code.add(a)
    hit = set()
    for e in seq:
        i = e.k
        if i.data or i.mn not in ('sta', 'stx', 'sty', 'inc', 'dec', 'asl', 'lsr', 'rol', 'ror'):
            continue
        if i.mode == 'abs' and i.arg in code:
            hit.add(i.arg)
        elif i.mode in ('abx', 'aby'):
            for a in range(i.arg, i.arg + 256):
                if a in code:
                    hit.add(a)
    return hit


def scan(seq, build):
    hits = defaultdict(list)       # pattern -> [(bytes, [E..], note, edits)]

    def add(p, nb, es, note='', edits=None):
        # a `skill-ok <PATTERN>` comment on any line of the hit = a documented exception
        # (the rule does not apply there: the loop's ORDER is the point, ...)
        for e in es:
            if ('skill-ok ' + p) in (e.k.src or ''):
                return
        hits[p].append((nb, es, note, edits))

    IX.update({(build, e.k.addr): k for k, e in enumerate(seq)})
    LABADDR[build] = sorted({x.addr for x in (e.k for e in seq) if x.labels})
    smc = smc_targets(seq)

    def smcd(i):
        return any(a in smc for a in range(i.addr, i.addr + i.size))

    n = len(seq)
    for k in range(n):
        e = seq[k]
        if e.i is None:
            continue
        i = e.i
        mn = i.mn
        w2, w3 = run(seq, k, 2), run(seq, k, 3)
        # KNOWNC --------------------------------------------------------------
        if mn in ('bcc', 'bcs'):
            want = 'sec' if mn == 'bcc' else 'clc'
            j = k + 1
            while j < n and run(seq, k, j - k + 1):
                x = seq[j].i
                if x.mn == want:
                    add('KNOWNC', 1, [e, seq[j]], '%s after a not-taken %s' % (want, mn),
                        [(seq[j], 'drop')])
                    break
                if x.mn in C_KEEP and not smcd(x):
                    j += 1
                    continue
                if x.mn in ('clc', 'sec') and x.mn != want:
                    break
                # CKNOWN: C proven here and the next is clc/adc #lit or sec/sbc #lit
                break
            # CKNOWN
            cval = 1 if mn == 'bcc' else 0
            j = k + 1
            while j < n and run(seq, k, j - k + 2):
                x, y = seq[j].i, seq[j + 1].i
                if cval == 1 and x.mn == 'clc' and y.mn == 'adc' and y.mode == 'imm' \
                        and LITERAL.search(y.src.split(';')[0]) and y.arg >= 1 and not smcd(y):
                    add('CKNOWN', 1, [e, seq[j], seq[j + 1]], 'C=1: adc #$%02X' % (y.arg - 1),
                        [(seq[j], 'drop'), (seq[j + 1], ('imm', y.arg - 1))])
                    break
                if cval == 0 and x.mn == 'sec' and y.mn == 'sbc' and y.mode == 'imm' \
                        and LITERAL.search(y.src.split(';')[0]) and y.arg >= 1 and not smcd(y):
                    add('CKNOWN', 1, [e, seq[j], seq[j + 1]], 'C=0: sbc #$%02X' % (y.arg - 1),
                        [(seq[j], 'drop'), (seq[j + 1], ('imm', y.arg - 1))])
                    break
                if x.mn in C_KEEP and not smcd(x):
                    j += 1
                    continue
                break
        # CLCASL --------------------------------------------------------------
        if mn == 'and' and i.mode == 'imm' and LITERAL.search(i.src.split(';')[0]) and not smcd(i):
            j, v = k + 1, i.arg
            while j < n and run(seq, k, j - k + 1) and seq[j].i.mn == 'asl' and seq[j].i.mode == 'acc':
                v <<= 1
                j += 1
            if j > k + 1 and v < 0x100 and j < n and run(seq, k, j - k + 1) and seq[j].i.mn == 'clc':
                add('CLCASL', 1, [e, seq[j]], 'and #$%X << %d < $100' % (i.arg, j - k - 1),
                    [(seq[j], 'drop')])
        if w2:
            a, b = w2
            am, bm = a.i.mn, b.i.mn
            # JSRRTS ----------------------------------------------------------
            if am == 'jsr' and bm == 'rts':
                add('JSRRTS', 1, [a, b], 'jmp', [(a, ('mn', 'jmp')), (b, 'drop')])
            # CMP0 ------------------------------------------------------------
            for r, cm in (('A', 'cmp'), ('X', 'cpx'), ('Y', 'cpy')):
                if am in NZ_FROM[r] and bm == cm and b.i.mode == 'imm' and b.i.arg == 0 \
                        and ZERO.search(b.i.src.split(';')[0]) and not smcd(b.i):
                    if c_dead(seq, k + 2):
                        add('CMP0', 2, [a, b], 'C dead after', [(b, 'drop')])
        # BRINV ---------------------------------------------------------------
        if mn in INVERT and k + 1 < n and seq[k + 1].i is not None and not seq[k + 1].entry:
            b = seq[k + 1].i
            if b.mn == 'jmp' and b.mode == 'abs' and b.addr == i.addr + 2 \
                    and i.arg == b.addr + 3 and not smcd(b):
                d = b.arg - (i.addr + 2)
                if -128 + MARGIN <= d <= 127 - MARGIN:
                    add('BRINV', 3, [e, seq[k + 1]], INVERT[mn],
                        [(e, ('br', INVERT[mn], seq[k + 1])), (seq[k + 1], 'drop')])
        # JMPNXT --------------------------------------------------------------
        if mn == 'jmp' and i.mode == 'abs' and i.arg == i.addr + 3 and not smcd(i):
            add('JMPNXT', 3, [e], '', [(e, 'drop')])
        if w3:
            a, b, c = w3
            # RELOAD ----------------------------------------------------------
            pair = {'sta': ('lda', 'A'), 'stx': ('ldx', 'X'), 'sty': ('ldy', 'Y')}
            if b.i.mn in pair and c.i.mn == pair[b.i.mn][0] and b.i.mode == c.i.mode \
                    and b.i.mode in ('zp', 'abs') and b.i.arg == c.i.arg and not volatile(b.i) \
                    and not smcd(b.i) and not smcd(c.i):
                r = pair[b.i.mn][1]
                prod_ok = a.i.mn in NZ_FROM[r] or (r == 'A' and a.i.mode == 'acc'
                                                   and a.i.mn in ('asl', 'lsr', 'rol', 'ror'))
                if prod_ok or nz_dead(seq, k + 3):
                    add('RELOAD', c.i.size, [b, c], 'flags = producer' if prod_ok else 'N/Z dead',
                        [(c, 'drop')])
        # DUPIMM --------------------------------------------------------------
        for r, ld, st in (('A', 'lda', 'sta'), ('X', 'ldx', 'stx'), ('Y', 'ldy', 'sty')):
            if mn == ld and i.mode == 'imm' and LITERAL.search(i.src.split(';')[0]) and not smcd(i):
                j = k + 1
                while j < n and run(seq, k, j - k + 1):
                    x = seq[j].i
                    if x.mn == ld and x.mode == 'imm' and x.arg == i.arg \
                            and LITERAL.search(x.src.split(';')[0]) and not smcd(x):
                        # flags: nothing between set N/Z (only stores) -> identical
                        between = [seq[t].i for t in range(k + 1, j)]
                        if all(y.mn in ('sta', 'stx', 'sty') for y in between) or nz_dead(seq, j + 1):
                            add('DUPIMM', 2, [e, seq[j]], '%s #$%02X again' % (ld, i.arg),
                                [(seq[j], 'drop')])
                        break
                    if x.mn in ('sta', 'stx', 'sty', 'clc', 'sec', 'cli', 'sei', 'clv', 'nop') \
                            or (r != 'A' and x.mn in ('lda', 'and', 'ora', 'eor', 'adc', 'sbc', 'cmp')
                                and x.mode not in ('abx' if r == 'X' else 'aby', 'zpx' if r == 'X' else 'zpy',
                                                   'izx', 'izy')) \
                            or (r != 'X' and x.mn in ('ldx', 'inx', 'dex', 'cpx', 'tsx')) \
                            or (r != 'Y' and x.mn in ('ldy', 'iny', 'dey', 'cpy')):
                        if (r == 'A' and x.mn in ('lda', 'and', 'ora', 'eor', 'adc', 'sbc', 'txa', 'tya', 'pla')) \
                                or (r == 'X' and x.mn in ('ldx', 'inx', 'dex', 'tax', 'tsx')) \
                                or (r == 'Y' and x.mn in ('ldy', 'iny', 'dey', 'tay')):
                            break
                        j += 1
                        continue
                    break
        # STXY0 ---------------------------------------------------------------
        if mn == 'lda' and i.mode == 'imm' and i.arg == 0 and ZERO.search(i.src.split(';')[0]) and not smcd(i):
            j = k + 1
            while j < n and run(seq, k, j - k + 1) and seq[j].i.mn == 'sta' and seq[j].i.mode in ('zp', 'abs'):
                j += 1
            if j > k + 1:
                # is X or Y proven 0 on arrival? walk back on the straight line
                for r, ld, st in (('X', 'ldx', 'stx'), ('Y', 'ldy', 'sty')):
                    z = zero_before(seq, k, r)
                    if z and reg_dead(seq, j, 'A') and nz_dead(seq, j):
                        ed = [(seq[k], 'drop')] + [(seq[t], ('mn', st)) for t in range(k + 1, j)]
                        add('STXY0', 2, seq[k:j], '%s = 0 (%s)' % (r, z), ed)
                        break
        # ---- REPORT -------------------------------------------------------------
        if w2:
            a, b = w2
            if a.i.mn in ('sta', 'stx', 'sty') and b.i.mn in ('asl', 'lsr', 'rol', 'ror') \
                    and b.i.mode == a.i.mode and b.i.arg == a.i.arg and a.i.mode in ('zp', 'abs'):
                add('MSHIFT', 0, [a, b], 'shift in A, store once')
            if a.i.mode in ('zp', 'abs') and b.i.mode == a.i.mode and a.i.arg is not None:
                if (a.i.mn == 'asl' and b.i.mn == 'rol' and b.i.arg == a.i.arg + 1) or \
                        (a.i.mn == 'lsr' and b.i.mn == 'ror' and b.i.arg == a.i.arg - 1):
                    add('MPAIR', 0, [a, b], '16-bit shift in memory (10-12 cyc/bit)')
        if w3:
            a, b, c = w3
            if a.i.mn in ('inx', 'iny') and b.i.mn in ('cpx', 'cpy') and b.i.mode == 'imm' \
                    and c.i.mn in ('bne', 'bcc') and c.i.arg < a.i.addr:
                add('LOOPCP', 0, [a, b, c], 'n=$%02X: count down, drop the compare' % b.i.arg)
        if mn in ('lsr', 'asl') and i.mode == 'acc' and not (k and seq[k - 1].i is not None
                                                              and seq[k - 1].i.mn == mn
                                                              and seq[k - 1].i.mode == 'acc'
                                                              and not e.entry):
            j = k
            while j < n and run(seq, k, j - k + 1) and seq[j].i.mn == mn and seq[j].i.mode == 'acc':
                j += 1
            if j - k >= 5:
                add('LSR5', 0, seq[k:j], '%d x %s' % (j - k, mn))
        if mn in ('txa', 'tya') and w2 and w2[1].i.mn == 'pha':
            back = 'tax' if mn == 'txa' else 'tay'
            for j in range(k + 2, min(n, k + 40)):
                x = seq[j].k
                if x.mn == 'pla' and j + 1 < n and seq[j + 1].k.mn == back:
                    add('SAVEX', 0, [e, seq[j + 1]], '%s saved on the stack' % mn[1].upper())
                    break
                if x.mn in ('rts', 'rti', 'jmp'):
                    break
        if mn in ('inc', 'dec', 'asl', 'lsr', 'rol', 'ror') and i.mode in ('abs', 'abx') \
                and 0xD000 <= i.arg <= 0xD7FF:
            add('HWRMW', 0, [e], 'RMW on a hardware register')
        # MASKSH : and #mask, then shifts that drop exactly the masked bits ------
        if mn == 'and' and i.mode == 'imm':
            j, sh = k + 1, 0
            while j < n and seq[j].k.mn in ('lsr', 'asl') and seq[j].k.mode == 'acc' \
                    and (sh == 0 or seq[j].k.mn == seq[k + 1].k.mn):
                sh += 1
                j += 1
            if sh:
                drop = ((1 << sh) - 1) if seq[k + 1].k.mn == 'lsr' else (0x100 - (1 << (8 - sh)))
                if i.arg | drop == 0xFF and i.arg != 0xFF:
                    add('MASKSH', 2, [e], 'and #$%02X before %d x %s drops those bits'
                        % (i.arg, sh, seq[k + 1].k.mn))
        # CMPSBC : cmp M / bcc out / sec / sbc M ---------------------------------
        if mn == 'cmp' and w3 and seq[k + 1].i.mn in ('bcc', 'bcs') \
                and seq[k + 2].i.mn == 'sec' and k + 3 < n and seq[k + 3].i is not None \
                and seq[k + 3].i.mn == 'sbc' and seq[k + 3].i.arg == i.arg \
                and seq[k + 3].i.mode == i.mode:
            add('CMPSBC', 3, seq[k:k + 4], 'the compare is the subtract')
        # DEC16 : a 16-bit decrement written as a borrow chain --------------------
        if mn == 'sec' and k + 5 < n and run(seq, k, 6):
            m = [seq[k + t].i for t in range(1, 6)]
            if m[0].mn == 'lda' and m[1].mn == 'sbc' and m[1].mode == 'imm' and m[1].arg == 1 \
                    and m[2].mn == 'sta' and m[2].arg == m[0].arg and m[3].mn == 'lda' \
                    and m[3].arg == m[0].arg + 1:
                add('DEC16', 4, seq[k:k + 6], 'lda lo / bne + / dec hi / + dec lo is shorter')
        # PHAPLA : a value parked on the stack across a call ---------------------
        if mn == 'pha':
            call = False
            for j in range(k + 1, min(n, k + 30)):
                x = seq[j].k
                if x.data or x.mn is None:
                    break
                if x.mn == 'jsr':
                    call = True
                if x.mn == 'pla':
                    if call:                 # parked ACROSS A CALL: zp (6) beats 11
                        add('PHAPLA', 0, [e, seq[j]], 'parked across a jsr')
                    break
                if x.mn in ('rts', 'rti', 'pha'):
                    break                    # (an IRQ's own pha/pla is mandatory)
        # JMPIND : the NMOS vector-at-page-end bug -------------------------------
        if mn == 'jmp' and i.mode == 'ind' and (i.arg & 0xFF) == 0xFF:
            add('JMPIND', 0, [e], 'jmp ($%04X): the high byte comes from $%04X'
                % (i.arg, i.arg & 0xFF00))
        # BWAIT ---------------------------------------------------------------
        if (mn == 'sta' and i.mode == 'abs' and i.arg == BL_START) or \
                (mn == 'jsr' and i.mode == 'abs' and i.arg == LAB[build].get('fire_fill', -1)):
            for j in range(k + 1, min(n, k + 12)):
                x = seq[j].k
                if x.data or x.mn is None:
                    break
                if (x.mn == 'jsr' and x.arg == LAB[build].get('blit_idle', -2)) or \
                        (x.mn == 'lda' and x.arg == BL_START):
                    add('BWAIT', 0, [e, seq[j]], 'wait %d instr after the START' % (j - k))
                    break
                if x.mn in ('rts', 'rti', 'jmp') or x.mn in BRANCH:
                    break
        # BCBK ----------------------------------------------------------------
        if mn in ('sta', 'stx', 'sty') and i.mode == 'abs' and BCB_LO <= i.arg <= BCB_HI \
                and k and seq[k - 1].i is not None and seq[k - 1].i.mode == 'imm' \
                and seq[k - 1].i.mn == 'l' + mn[1:].replace('t', 'd', 1):
            fld = i.arg - BCB_LO
            add('BCBK', 0, [seq[k - 1], e], 'BCB+%d = #$%02X' % (fld, seq[k - 1].i.arg))
            if fld == 13 and seq[k - 1].i.arg & 0xFE:
                add('BWIDTH', 0, [seq[k - 1], e], 'WIDTH hi = $%02X: only bit 0 exists' % seq[k - 1].i.arg)
    # TEMPRD / CMPSBCG / JMPFLAG : drac030-review --------------------------------
    WR = {'A': {'lda', 'pla', 'txa', 'tya', 'adc', 'sbc', 'and', 'ora', 'eor'},
          'X': {'ldx', 'tax', 'inx', 'dex', 'tsx'}, 'Y': {'ldy', 'tay', 'iny', 'dey'}}
    MWR = {'sta', 'stx', 'sty', 'inc', 'dec', 'asl', 'lsr', 'rol', 'ror'}
    pair = {'sta': ('lda', 'A'), 'stx': ('ldx', 'X'), 'sty': ('ldy', 'Y')}
    for k, e in enumerate(seq):
        i = e.i
        if i is None:
            continue
        if i.mn in pair and i.mode in ('zp', 'abs') and not volatile(i) and not smcd(i):
            ld, r = pair[i.mn]
            j = k + 1
            while j < n and j < k + 14 and run(seq, k, j - k + 1):
                x = seq[j].i
                if x.mn == ld and x.mode == i.mode and x.arg == i.arg:
                    if j > k + 1 and not smcd(x):
                        add('TEMPRD', x.size, [e, seq[j]], '%d instr between' % (j - k - 1))
                    break
                if x.mn in WR[r] or (r == 'A' and x.mode == 'acc') or x.mn in FLOW                         or (x.mn in MWR and x.arg == i.arg) or x.mode in ('abx', 'aby', 'izy', 'izx')                         and x.mn in MWR:
                    break
                j += 1
        if i.mn == 'cmp' and i.mode in ('zp', 'abs', 'imm') and k + 1 < n and seq[k + 1].i is not None                 and seq[k + 1].i.mn in ('bcc', 'bcs'):
            j = k + 2
            while j + 1 < n and j < k + 10 and run(seq, k, j - k + 2):
                x, y = seq[j].i, seq[j + 1].i
                if x.mn == 'sec' and y.mn == 'sbc' and y.mode == i.mode and y.arg == i.arg and j > k + 2:
                    add('CMPSBCG', 2, seq[k:j + 2], 'subtract first, branch on its carry')
                    break
                if x.mn in WR['A'] or x.mode == 'acc' or x.mn in FLOW or x.mn in C_WRITE or x.mn in C_READ:
                    break
                j += 1
        if i.mn == 'jmp' and i.mode == 'abs' and -120 <= i.arg - (i.addr + 2) <= 120:
            # walk back on the straight line to a not-taken conditional branch
            j, fl = k - 1, None
            while j >= 0 and k - j < 12 and seq[j].i is not None and not seq[j + 1].entry:
                x = seq[j].i
                if x.mn in INVERT:
                    fl = x.mn
                    break
                if x.mn in FLOW:
                    break
                if x.mn in C_WRITE or x.mn in C_READ or x.mn in NZ_SETTERS:
                    # only a carry flag survives NZ setters that keep C
                    if x.mn not in C_KEEP:
                        break
                j -= 1
            if fl in ('bcc', 'bcs') and not any(seq[t].i.mn in C_WRITE for t in range(j + 1, k)):
                add('JMPFLAG', 1, [seq[j], e], 'C known: %s' % INVERT[fl])
            elif fl in ('bvc', 'bvs') and all(seq[t].i.mn in C_KEEP - {'bit'} for t in range(j + 1, k)):
                add('JMPFLAG', 1, [seq[j], e], 'V known: %s' % INVERT[fl])
            elif fl and all(seq[t].i.mn in ('sta', 'stx', 'sty', 'clc', 'sec', 'pha', 'txs', 'nop')
                            for t in range(j + 1, k)):
                add('JMPFLAG', 1, [seq[j], e], 'flag known: %s' % INVERT[fl])
    # MADSMAC : the MADS macro commands expand to plain 8-bit sequences
    for k, e in enumerate(seq):
        i = e.k
        if i.data or i.mn is None or not text_ok(i):
            continue
        src = (i.src or '').split(';')[0].strip().lower()
        first = src.split()[0] if src else ''
        if first in ('mwa', 'mva', 'mvx', 'mvy', 'adw', 'sbw', 'inw', 'dew', 'cpw', 'ldx+',
                     'add', 'sub') and per_line[(i.file, i.line)] > 1:
            add('MADSMAC', 0, [e], '`%s` expands to several instructions' % first)
    # TABX : an indexed table read inside a loop, and the table straddles a page
    for k, e in enumerate(seq):
        i = e.k
        if i.data or i.mn is None or i.mode not in ('abx', 'aby') or i.mn not in \
                ('lda', 'ldx', 'ldy', 'adc', 'sbc', 'and', 'ora', 'eor', 'cmp'):
            continue
        if i.arg < 0x200 or 0xD000 <= i.arg <= 0xD7FF or smcd(i):
            continue                         # (an SMC-patched base indexes itself)
        # A table's size is not in the listing (most are equates), so flag only the
        # bases that cross for ANY plausible table: the last page bytes (a `tab-1` at
        # a page start lands here) and anything a 64-entry table would run past.
        nxt = min([a for a in LABADDR[build] if a > i.arg] or [i.arg + 256])
        if (i.arg & 0xFF) + min(nxt - i.arg, 256) <= 0x100:
            continue
        if (i.arg & 0xFF) <= 0xC0:       # a 64-entry table at $xxC0 ends at $xxFF
            continue
        # inside a loop? a backward branch within 40 instructions after it
        loop = False
        for j in range(k + 1, min(len(seq), k + 40)):
            x = seq[j].k
            if x.mn in BRANCH and x.arg is not None and x.arg <= i.addr:
                loop = True
                break
            if x.mn in ('rts', 'rti'):
                break
        if loop and (i.arg & 0xFF):
            add('TABX', 0, [e], 'table at $%04X is not page-aligned (+1 when a read crosses)'
                % i.arg)
    # PAGEX / BRPAGE: every branch, macro expansions included (mfetch's `bne *+5`)
    rout = '?'
    for k, e in enumerate(seq):
        i = e.k
        if i.data or i.mn is None:
            continue
        rout = next((nm for nm in i.labels if not nm.startswith('?')), rout)
        if i.mn not in BRANCH or not ((i.addr + 2) ^ i.arg) & 0xFF00:
            continue
        if i.arg < i.addr:
            # a BUSY spin-wait costs nothing when the blitter is idle (the branch is
            # taken only while it is busy, and then the CPU is waiting anyway)
            if any(x.k.arg == 0xD653 for x in seq[max(0, k - 3):k] if x.k.mn == 'lda' or x.k.mn == 'ora'):
                continue
            add('PAGEX', 0, [e], 'in %s: loop edge crosses a page (+1 per pass)' % rout)
        else:
            skip = i.arg - i.addr - 2 == 3 and k + 1 < n and seq[k + 1].k.mn == 'jsr'
            add('BRPAGE', 0, [e], 'in %s: -> $%04X (+1 when taken)%s'
                % (rout, i.arg, '  SKIP, taken ~always' if skip else ''))
    # TINY: routines cheaper than their call
    starts = {}
    for k, e in enumerate(seq):
        i = e.k
        if i.data or i.mn is None:
            continue
        for nm in i.labels:
            if not nm.startswith('?'):
                starts[i.addr] = (k, nm)
    calls = Counter(e.k.arg for e in seq if e.k.mn == 'jsr')
    for addr, cnt in calls.items():
        if addr not in starts:
            continue
        k, nm = starts[addr]
        cyc, j = 0, k
        ok = False
        while j < n and j < k + 8:
            x = seq[j].k
            if x.data or x.mn is None:
                break
            if x.mn == 'rts':
                ok = True
                break
            if x.mn in FLOW:
                break
            cyc += LS.cycles(x.mn, x.mode)
            j += 1
        if ok and cyc < 12:
            add('TINY', 0, [seq[k]], '%s: body %d cyc < 12-cyc call, %d call site(s)' % (nm, cyc, cnt))
    return hits


def zero_before(seq, k, r):
    """Is register r proven 0 on arrival at seq[k] (straight line back, no entry)?"""
    ld, kills = {'X': ('ldx', {'ldx', 'tax', 'tsx', 'inx', 'dex'}),
                 'Y': ('ldy', {'ldy', 'tay', 'iny', 'dey'})}[r]
    j = k
    if seq[j].entry:
        return None
    while j > 0:
        j -= 1
        x = seq[j].i
        if x is None:
            return None
        if x.mn == ld and x.mode == 'imm' and x.arg == 0 and ZERO.search(x.src.split(';')[0]):
            return '%s #0' % ld
        if x.mn == 'bne' and j > 0 and seq[j - 1].i is not None and \
                seq[j - 1].i.mn == ('dex' if r == 'X' else 'dey') and not seq[j].entry:
            return 'fall-through of %s / bne' % seq[j - 1].i.mn
        if x.mn in kills or x.mn in FLOW or x.mn in ('jsr',):
            return None
        if seq[j].entry:
            return None
    return None


# ---- source rewriting ----------------------------------------------------------
def drop(row, mn):
    """Comment the instruction out where it stands; a label in front stays."""
    m = re.search(r'(?<![\w?@.])' + re.escape(mn) + r'\b', row.split(';')[0], re.I)
    if not m:
        return None
    return row[:m.start()] + ';' + row[m.start():]


def rewrite(row, e, what):
    code = row.split(';')[0]
    rest = row[len(code):]
    i = e.i
    if what == 'drop':
        return drop(row, i.mn)
    kind = what[0]
    if kind == 'mn':
        new, cnt = re.subn(r'(?<![\w?@.])' + i.mn + r'\b', what[1], code, count=1, flags=re.I)
    elif kind == 'imm':
        new, cnt = re.subn(r'#\s*(?:\$[0-9A-Fa-f]+|%[01]+|[0-9]+)\s*$', '#$%02X' % what[1],
                           code.rstrip(), count=1)
        if cnt:
            new = new + code[len(code.rstrip()):]
    elif kind == 'br':
        tgt = re.search(r'\bjmp\s+([^\s;]+)', what[2].i.src, re.I)
        if not tgt:
            return None
        new, cnt = re.subn(r'(?<![\w?@.])' + i.mn + r'(\s+)[^\s;]+', what[1] + r'\g<1>' + tgt.group(1),
                           code, count=1, flags=re.I)
    else:
        return None
    return new + rest if cnt == 1 else None


LAB = {}


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    if '--revert' in sys.argv:
        return revert()
    only = set(sys.argv[sys.argv.index('--only') + 1].split(',')) if '--only' in sys.argv else None
    allhits = {}
    seqs = {}
    for build in ('game', 'intro'):
        seq = load(build)
        LAB[build] = {}
        for e in seq:
            for nm in e.k.labels:
                LAB[build].setdefault(nm, e.k.addr)
        seqs[build] = seq
        allhits[build] = scan(seq, build)
    say('%-8s %6s %6s %6s' % ('pattern', 'game', 'intro', 'bytes'))
    for p in MECH + REPORT:
        g = allhits['game'].get(p, [])
        t = allhits['intro'].get(p, [])
        say('%-8s %6d %6d %6d%s' % (p, len(g), len(t), sum(x[0] for x in g) + sum(x[0] for x in t),
                                    '' if p in MECH else '   report only'))
    for p in MECH + REPORT:
        for build in ('game', 'intro'):
            h = allhits[build].get(p, [])
            if not h or '--summary' in sys.argv:
                continue
            say('\n-- %s (%s)' % (p, build))
            for nb, es, note, _ in h:
                x = es[0].k
                say('   %s:%d  $%04X  ' % (x.file, x.line, x.addr)
                    + ' / '.join(re.sub(r'\s+', ' ', y.k.src.split(';')[0].strip())[:28] for y in es[:5])
                    + (' / ...' if len(es) > 5 else '') + ('   [%s]' % note if note else ''))
    open(os.path.join(OUTDIR, 'skill_tips.txt'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    if '--apply' in sys.argv:
        apply(allhits, seqs, only)


def apply(allhits, seqs, only):
    # which (file, line) is assembled in which build
    lines_in = {b: {(e.k.file, e.k.line) for e in seqs[b]} for b in seqs}
    plans = {b: {} for b in seqs}           # (file,line) -> (pattern, new text)
    for b in seqs:
        for p in MECH:
            if only and p not in only:
                continue
            for nb, es, note, edits in allhits[b].get(p, []):
                staged = []
                ok = edits is not None
                for e, what in edits or ():
                    if e.i is None or not text_ok(e.i):
                        ok = False
                        break
                    fs = LS._file_lines(e.i.file)
                    row = fs[e.i.line - 1]
                    new = rewrite(row, e, what)
                    if new is None:
                        ok = False
                        break
                    staged.append(((e.i.file, e.i.line), new, row))
                if not ok:
                    continue
                if any(key in plans[b] for key, _, _ in staged):
                    continue
                for key, new, row in staged:
                    plans[b][key] = (p, new, row)
    final = {}
    skipped = Counter()
    for b in plans:
        other = 'intro' if b == 'game' else 'game'
        for key, (p, new, row) in plans[b].items():
            if key in lines_in[other]:
                o = plans[other].get(key)
                if o is None or o[1] != new:
                    skipped[p] += 1
                    continue
            final[key] = (p, new, row)
    files = defaultdict(dict)
    for (fn, ln), v in final.items():
        files[fn][ln] = v
    rec = []
    done = Counter()
    for fn, ed in files.items():
        path = os.path.join(PROJ, fn)
        raw = open(path, 'rb').read().decode('latin-1')
        eol = '\r\n' if '\r\n' in raw else '\n'
        rows = raw.split(eol)
        for ln, (p, new, row) in sorted(ed.items()):
            assert rows[ln - 1] == row, (fn, ln)
            rows[ln - 1] = new
            rec.append('%s:%d\t%s\t%s' % (fn, ln, p, row))
            done[p] += 1
        open(path, 'wb').write(eol.join(rows).encode('latin-1'))
    with open(APPLIED, 'a', encoding='latin-1', newline='') as fh:
        for r in rec:
            fh.write(r + '\n')
    print('applied: %s = %d line(s) in %d file(s); skipped (shared file, builds disagree): %s'
          % (dict(done), len(rec), len(files), dict(skipped) or 0))


def revert():
    n, files = 0, defaultdict(dict)
    if not os.path.exists(APPLIED):
        print('nothing applied')
        return
    for r in open(APPLIED, encoding='latin-1', newline=''):
        w = r.rstrip('\r\n').split('\t', 2)
        if len(w) != 3:
            continue
        fn, no = w[0].rsplit(':', 1)
        files[fn][int(no)] = w[2]
    for fn, ed in files.items():
        path = os.path.join(PROJ, fn)
        raw = open(path, 'rb').read().decode('latin-1')
        eol = '\r\n' if '\r\n' in raw else '\n'
        rows = raw.split(eol)
        for ln, row in ed.items():
            rows[ln - 1] = row
            n += 1
        open(path, 'wb').write(eol.join(rows).encode('latin-1'))
    os.remove(APPLIED)
    print('%d line(s) restored in %d file(s)' % (n, len(files)))


if __name__ == '__main__':
    main()
