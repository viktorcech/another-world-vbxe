#!/usr/bin/env python3
"""verify_covox.py - build guard for the COVOX output mode in src/aw_sound.asm.

snd_go_covox rewrites live 6502 code: it copies covox instruction images over
the POKEY ones assembled in place (see cvpatch). The assembler cannot check
that, because both sides are legal on their own -- what it cannot see is a slot
whose two versions have DIFFERENT lengths. A covox image one byte too long
silently eats the first byte of the next instruction, and the failure mode is
an intro that plays fine for everyone without a covox and crashes on the one
machine that has one. So: replay the patch loop on the assembled binary and
disassemble what comes out.

Checks (generic -- driven by the cvpatch table itself, so it covers BOTH the
intro's player, src/aw_sound.asm, and the game's, src_game/game_sound.asm):
  1. every cvpatch entry lands in CPU RAM, clear of the MEMAC windows, and no
     two entries overlap
  2. every code slot (length >= 3) decodes to whole instructions consuming
     EXACTLY its length -- on BOTH sides, so neither version can run off the end
  3. no slot decodes to an unknown opcode
  4. operand-only patches (length <= 2) stay inside their own instruction
  5. cv_irq's `jmp ...body` lands on the shared body entry, writes both covox
     ports, and does so near the head (the fixed-offset DAC write is the whole
     reason that prologue exists)
Plus, when the intro's labels are present, its specific slot shapes.

Usage:   python tools/verify_covox.py <xex> <lst>
         (defaults to out/_cv.xex + out/_cv.lst, the ad-hoc assembly used while
          developing; build.ps1 passes the real pairs)
"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CODE_LO, CODE_HI = 0x2000, 0x4000       # the intro's 6502 code window


# --- XEX -> flat memory ------------------------------------------------------
def load_xex(path):
    d = open(path, 'rb').read()
    mem = bytearray(0x10000)
    seen = bytearray(0x10000)
    p = 0
    if d[0:2] == b'\xff\xff':
        p = 2
    while p + 4 <= len(d):
        if d[p:p + 2] == b'\xff\xff':
            p += 2
            continue
        start = d[p] | (d[p + 1] << 8)
        end = d[p + 2] | (d[p + 3] << 8)
        p += 4
        n = end - start + 1
        if n <= 0 or p + n > len(d):
            break
        # INI/RUN vectors are control, not content
        if start not in (0x02E0, 0x02E2):
            mem[start:start + n] = d[p:p + n]
            for a in range(start, start + n):
                seen[a] = 1
        p += n
    return mem, seen


# --- mads listing -> label addresses -----------------------------------------
LINE = re.compile(r'^\s*\d+\s+([0-9A-F]{4})\b(.*)$')


def load_labels(path):
    lab = {}
    for line in open(path, encoding='utf-8', errors='replace'):
        m = LINE.match(line)
        if not m:
            continue
        addr = int(m.group(1), 16)
        src = m.group(2).split('\t')[-1]
        if src.startswith('.proc') or src.startswith('.local'):
            parts = src.split()
            if len(parts) > 1:
                lab.setdefault(parts[1], addr)
            continue
        if not src or src[0].isspace() or src[0] in ';.?':
            continue
        name = src.split()[0]
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            lab.setdefault(name, addr)
    return lab


# --- just enough 6502 to walk these slots ------------------------------------
#   opcode -> (mnemonic, total length)
OPS = {
    0x0A: ('asl', 1), 0x18: ('clc', 1), 0x28: ('plp', 1), 0x40: ('rti', 1),
    0x48: ('pha', 1), 0x60: ('rts', 1), 0x68: ('pla', 1), 0x88: ('dey', 1),
    0xC8: ('iny', 1), 0xE8: ('inx', 1), 0xEA: ('nop', 1), 0x4A: ('lsr', 1),
    0xA9: ('lda#', 2), 0x29: ('and#', 2), 0x09: ('ora#', 2), 0x69: ('adc#', 2),
    0xC9: ('cmp#', 2), 0xA2: ('ldx#', 2), 0xA0: ('ldy#', 2), 0xE0: ('cpx#', 2),
    0xA5: ('lda zp', 2), 0x85: ('sta zp', 2), 0x65: ('adc zp', 2),
    0xE6: ('inc zp', 2), 0x24: ('bit zp', 2),
    0xD0: ('bne', 2), 0xF0: ('beq', 2), 0x10: ('bpl', 2), 0x30: ('bmi', 2),
    0x90: ('bcc', 2), 0xB0: ('bcs', 2),
    0xAD: ('lda abs', 3), 0x8D: ('sta abs', 3), 0x2D: ('and abs', 3),
    0x2C: ('bit abs', 3), 0x8E: ('stx abs', 3), 0xAE: ('ldx abs', 3),
    0xEE: ('inc abs', 3), 0xEC: ('cpx abs', 3), 0x4C: ('jmp', 3),
    0x6C: ('jmp ()', 3), 0x20: ('jsr', 3), 0xBD: ('lda abs,x', 3),
    0x9D: ('sta abs,x', 3), 0xB9: ('lda abs,y', 3), 0x99: ('sta abs,y', 3),
}


def walk(mem, addr, limit):
    """Decode instructions from addr for `limit` bytes.
    Returns (list of (addr, mnemonic, operand), bytes actually consumed)."""
    out, p = [], addr
    while p < addr + limit:
        op = mem[p]
        if op not in OPS:
            out.append((p, f'??${op:02X}', None))
            return out, p + 1 - addr
        mn, n = OPS[op]
        val = None
        if n == 2:
            val = mem[p + 1]
        elif n == 3:
            val = mem[p + 1] | (mem[p + 2] << 8)
        out.append((p, mn, val))
        p += n
    return out, p - addr


fails, notes = [], []


def check(cond, msg):
    (notes if cond else fails).append(msg)


def report():
    for m in notes:
        print(f'  ok   {m}')
    for m in fails:
        print(f'  FAIL {m}')
    print(f'\nverify_covox: {len(notes)} ok, {len(fails)} failed')
    return 1 if fails else 0


def main():
    xex = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'out', '_cv.xex')
    lst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, 'out', '_cv.lst')
    for f in (xex, lst):
        if not os.path.exists(f):
            sys.exit(f'verify_covox: {f} missing -- assemble the intro first')

    mem, _ = load_xex(xex)
    lab = load_labels(lst)

    for n in ('cvpatch', 'cv_irq', 'body'):
        if n not in lab:
            sys.exit(f'verify_covox: label {n} not found in {lst}')
    tab = lab['cvpatch']
    intro = 'cvtail' in lab          # the intro player has the two-voice mix tail

    # ---- 1. replay the patch loop -------------------------------------------
    patched = bytearray(mem)
    entries, p = [], tab
    while True:
        dest = mem[p] | (mem[p + 1] << 8)
        if mem[p + 1] == 0:
            break
        n = mem[p + 2]
        src = mem[p + 3] | (mem[p + 4] << 8)
        p += 5
        entries.append((dest, n, src))
        patched[dest:dest + n] = mem[src:src + n]
        if len(entries) > 64:
            sys.exit('verify_covox: cvpatch has no terminator (runaway table)')

    check(len(entries) > 0, f'cvpatch: {len(entries)} entries, terminated')

    covered = {}
    for dest, n, src in entries:
        # Must be CPU RAM the patch can actually reach: above the boot loader's
        # area, below OS ROM, and clear of the MEMAC-B ($4000-$7FFF) / MEMAC-A
        # ($8000-$8FFF) windows, where VRAM shadows the RAM underneath.
        lo, hi = dest, dest + n
        ok = (0x0900 <= lo and hi <= 0xC000
              and not (lo < 0x9000 and hi > 0x4000))
        check(ok, f'entry ${dest:04X}+{n} in patchable RAM')
        for a in range(dest, dest + n):
            if a in covered:
                fails.append(f'entry ${dest:04X}+{n} OVERLAPS ${covered[a]:04X}')
            covered[a] = dest

    # ---- 2/3. every code slot decodes to whole instructions, both versions --
    # This is the check the assembler cannot do: a covox image one byte too long
    # silently eats the first byte of the next instruction, and it breaks ONLY
    # on the machines that have a covox.
    # A slot is clean when the decode either lands EXACTLY on the boundary, or
    # ends control flow (rti/rts/jmp) before it -- the intro's tail slot is 7
    # bytes of POKEY code plus dead padding that only the covox version fills.
    ENDS = ('rti', 'rts', 'jmp', 'jmp ()')
    for dest, n, src in entries:
        if n < 3:
            continue                          # operand patch, checked below
        for tag, mem_ in (('POKEY', mem), ('covox', patched)):
            ins, used = walk(mem_, dest, n)
            shape = [m for _, m, _ in ins]
            end = next((i for i, m in enumerate(shape) if m in ENDS), None)
            live = shape if end is None else shape[:end + 1]
            bad = [m for m in live if m.startswith('??')]
            check((end is not None or used == n) and not bad,
                  f'slot ${dest:04X}+{n} ({tag}): {live} '
                  + ('ends flow inside the slot' if end is not None
                     else f'consumes {used} B'))

    # ---- 5. the covox IRQ prologue (both players) --------------------------
    a = lab['cv_irq']
    ins, _ = walk(patched, a, 0x24)
    jmps = [(m, v) for _, m, v in ins if m == 'jmp']
    check(jmps and jmps[-1][1] == lab['body'],
          f'cv_irq jmp -> ${jmps[-1][1]:04X} (want body ${lab["body"]:04X})')
    stores = [v for _, m, v in ins if m == 'sta abs']
    check(0xD280 in stores and 0xD281 in stores,
          f'cv_irq writes both covox ports: {[hex(s) for s in stores]}')
    # the DAC write must sit at a FIXED offset from entry -- that is the whole
    # reason the prologue exists, so make sure nothing crept in front of it
    off = next(off for off, (_, m, v) in
               ((i[0] - a, i) for i in ins) if m == 'sta abs' and v == 0xD280)
    check(off < 0x20, f'cv_irq DAC store at entry+{off} B (must stay near the head)')

    # ---- 4. operand-only patches stay inside their instruction -------------
    for dest, n, src in entries:
        if n > 2:
            continue
        op = patched[dest - 1]
        if op in OPS:
            _, ln = OPS[op]
            check(ln - 1 >= n,
                  f'operand patch ${dest:04X}+{n} fits its opcode ${op:02X}')

    if not intro:
        return report()

    # ---- the intro player's specific slot shapes ---------------------------
    for name in ('mo0', 'mo1'):
        a = lab[name]
        ins, used = walk(patched, a, 5)
        shape = [m for _, m, _ in ins]
        check(used == 5 and shape == ['sta abs', 'nop', 'nop'],
              f'{name} @${a:04X}: {shape} in {used} B (want sta abs/nop/nop in 5)')
        mus_out = ins[0][2]
        check(mus_out < 0x100, f'{name} stores to mus_out ${mus_out:04X} (zero page)')

    # ---- 3. the IRQ tail ----------------------------------------------------
    a, end = lab['cvtail'], lab['cvtail_end']
    ins, used = walk(patched, a, end - a)
    shape = [m for _, m, _ in ins]
    want = ['lda zp', 'asl', 'asl', 'asl', 'adc zp', 'sta abs',
            'lda zp', 'sta abs', 'pla', 'rti']
    check(shape == want, f'cvtail @${a:04X}: {shape}')
    check(used == end - a,
          f'cvtail consumes {used} B, slot is {end - a} B (must match exactly)')

    # ---- 4. snd_mute --------------------------------------------------------
    a = lab['snd_mute']
    ins, used = walk(patched, a, 24)
    shape = [m for _, m, _ in ins]
    want = ['lda#', 'sta abs', 'lda#', 'sta abs', 'lda#', 'sta abs', 'sta abs',
            'sta abs', 'rts']
    check(shape[:len(want)] == want, f'snd_mute @${a:04X}: {shape[:len(want)]}')
    check(ins[0][2] == 8, f'snd_mute music silence = {ins[0][2]} (want 8)')
    check(ins[2][2] == 64, f'snd_mute SFX silence = {ins[2][2]} (want 64)')
    check(ins[4][2] == 0x80, f'snd_mute DAC park = ${ins[4][2]:02X} (want $80)')
    check(ins[5][2] == 0xD280 and ins[6][2] == 0xD281,
          f'snd_mute writes ${ins[5][2]:04X}/${ins[6][2]:04X} (want $D280/$D281)')

    # ---- each voice was re-pointed at ITS OWN accumulator -------------------
    # a cv_mus/cv_sfx mix-up in cvpatch assembles fine and merges the two
    # voices into one byte, so name the targets explicitly.
    mus_out = walk(patched, lab['mo0'], 5)[0][0][2]
    tgt = {}
    for name in ('so0', 'so1', 'ss1', 'ms1'):
        if name not in lab:
            fails.append(f'label {name} not in the listing')
            continue
        ins, _ = walk(patched, lab[name], 3)
        tgt[name] = ins[0][2]
        check(ins[0][1] == 'sta abs', f'{name} is still a store')
    check(tgt.get('ms1') == mus_out,
          f'music silence -> ${tgt.get("ms1", 0):04X} (want mus_out ${mus_out:04X})')
    sfx_out = tgt.get('so0')
    check(sfx_out is not None and sfx_out != mus_out,
          f'SFX accumulator ${sfx_out:04X} is distinct from mus_out ${mus_out:04X}')
    for name in ('so1', 'ss1'):
        check(tgt.get(name) == sfx_out,
              f'{name} -> ${tgt.get(name, 0):04X} (want sfx_out ${sfx_out:04X})')

    return report()


if __name__ == '__main__':
    sys.exit(main())
