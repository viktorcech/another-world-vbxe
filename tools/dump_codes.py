#!/usr/bin/env python3
"""dump_codes.py - extract the ACCESS-CODE -> (part, checkpoint) table from the
shipped part-16008 bytecode, the authoritative source for game_gui.py's CHECKPOINTS.

The password screen (part 16008) reads the 4 entered symbols into VAR30..VAR33, then
runs a compare chain (starting near $073D): each rung tests the 4 symbols against a
fixed quadruple and, on a match, does `VAR0 := pos` followed by op_memlist <part>
(AW's restartAt(part, pos) -- it primes VAR(0)=pos before the part's thread 0 runs).

This walks that chain straight out of the data and prints each code with its part and
VAR0 checkpoint, decoding the symbol indices back to letters and cross-checking every
code against the on-screen string table (aw_text.STRINGS 0x15E..). Run from tools/:

    python dump_codes.py
"""
import aw_pack, game_sim, aw_text

# code-wheel symbol index -> letter, derived from the compare chain vs the string table
SYM = {11: 'B', 12: 'C', 13: 'D', 15: 'F', 16: 'G', 17: 'H',
       19: 'J', 20: 'K', 21: 'L', 27: 'R', 29: 'T', 33: 'X'}


def load_16008_code():
    mem = aw_pack.read_memlist()
    _, co, _, _ = game_sim.MEMLIST_PARTS[16008 - 16000]
    return aw_pack.load_resource(mem[co])[0]


def decode(code):
    """Walk the bytecode; yield (symbols, part, pos) for each access-code rung.
    A rung is: condjmp VAR30!=a ; condjmp VAR31!=b ; condjmp VAR32!=c ; condjmp VAR33!=d ;
    movconst VAR0:=pos ; memlist PART. We detect it by the 4 consecutive VAR30..33 tests."""
    n = len(code)
    pc = 0

    def b():
        nonlocal pc; v = code[pc]; pc += 1; return v

    def w():
        nonlocal pc; v = (code[pc] << 8) | code[pc + 1]; pc += 2; return v

    def sw():
        v = w(); return v - 0x10000 if v & 0x8000 else v

    SZ = {0x00: 3, 0x01: 2, 0x02: 2, 0x03: 3, 0x04: 2, 0x05: 0, 0x06: 0, 0x07: 2,
          0x08: 3, 0x09: 3, 0x0B: 2, 0x0C: 3, 0x0D: 1, 0x0E: 2, 0x0F: 2, 0x10: 1,
          0x11: 0, 0x12: 5, 0x13: 2, 0x14: 3, 0x15: 3, 0x16: 3, 0x17: 3, 0x18: 5,
          0x19: 2, 0x1A: 5}

    while pc < n:
        op = code[pc]
        # a rung starts where VAR30..33 are tested in order, then VAR0:=pos, memlist PART
        if op == 0x0A and pc + 4 < n and code[pc + 2] == 30:
            syms = []
            for vi in (30, 31, 32, 33):
                pc += 1                       # op 0x0A
                sub = b(); var = b()
                rhs = b() if not (sub & 0xC0) else (sw() if (sub & 0x40) else b())
                w()                           # dst
                if var != vi:
                    syms = None; break
                syms.append(rhs if rhs < 0x8000 else rhs - 0x10000)
            if syms:
                # VAR0 := pos
                pos = None
                if code[pc] == 0x00 and code[pc + 1] == 0:
                    pc += 1; b(); pos = sw()
                # memlist PART
                if code[pc] == 0x19:
                    pc += 1; num = w()
                    if num >= 0x3E80:
                        yield syms, num - 0x3E80 + 16000, pos
                continue
        # otherwise skip this instruction
        pc += 1
        if op & 0xC0:                          # draw ops: variable length
            if op & 0x80:
                pc += 2
            else:
                x = code[pc]; pc += 1
                if not (op & 0x20) and not (op & 0x10):
                    pc += 1
                y = code[pc]; pc += 1
                if not (op & 8) and not (op & 4):
                    pc += 1
                if (op & 2) == 0 and (op & 1):
                    pc += 1
                elif (op & 2) and not (op & 1):
                    pc += 1
        else:
            pc += SZ.get(op, 0)


def main():
    code = load_16008_code()
    strs = {v: k for k, v in aw_text.STRINGS.items()}   # code text -> string id
    print(f"{'code':6} {'part':6} {'pos':>4}  {'strId':>5}  symbols")
    print('-' * 44)
    rows = []
    for syms, part, pos in decode(code):
        if all(s == -1 for s in syms):
            text = '(blank)'
        else:
            text = ''.join(SYM.get(s, '?') for s in syms)
        sid = strs.get(text)
        ok = '' if (text == '(blank)' or sid is not None) else '  <-- NOT in string table!'
        sids = f'0x{sid:X}' if sid is not None else '-'
        rows.append((part, pos if pos is not None else -1, text, sids, syms, ok))
    for part, pos, text, sids, syms, ok in sorted(rows, key=lambda r: (r[0], r[1])):
        print(f"{text:6} {part:6} {pos:>4}  {sids:>5}  {syms}{ok}")
    print(f"\n{len(rows)} access-code locations (the blank entry returns to the intro).")


if __name__ == '__main__':
    main()
