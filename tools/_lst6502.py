#!/usr/bin/env python3
"""_lst6502.py - read a MADS listing (out/awgame.lst, out/awintro.lst) into decoded
NMOS 6502 instructions. Only what the assembler EMITTED counts: lines of an inactive
.if/.else branch carry no address and are skipped; macro expansions are attributed to
the invoking source line; instruction sizes/modes come from the BYTES, not the text
(a `jeq` that MADS expanded to `bne *+5 / jmp` decodes as the two real instructions).

    Ins fields: addr, size, op, mn, mode, arg (zp/abs address, immediate value or
    branch target), src (source text), file (repo-relative path), line (source line
    the instruction came from), labels (names defined at this address), macro (bool),
    data (True for dta/ins/.byte lines -- opaque to flow analysis).
"""
import os
import re
from collections import namedtuple

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EOL = chr(13) + chr(10)
TAB = chr(9)

# ---- NMOS 6502 documented opcodes: op -> (mnemonic, mode) ------------------------
# modes: imp acc imm zp zpx zpy abs abx aby ind izx izy rel
OPC = {}
def _g(mn, pairs):
    for op, mode in pairs:
        OPC[op] = (mn, mode)
_ALU = [('ora', 0x00), ('and', 0x20), ('eor', 0x40), ('adc', 0x60), ('sta', 0x80),
        ('lda', 0xA0), ('cmp', 0xC0), ('sbc', 0xE0)]
for mn, b in _ALU:
    for off, mode in ((0x01, 'izx'), (0x05, 'zp'), (0x09, 'imm'), (0x0D, 'abs'),
                      (0x11, 'izy'), (0x15, 'zpx'), (0x19, 'aby'), (0x1D, 'abx')):
        if mn == 'sta' and mode == 'imm':
            continue
        OPC[b + off] = (mn, mode)
for mn, b in (('asl', 0x00), ('rol', 0x20), ('lsr', 0x40), ('ror', 0x60)):
    _g(mn, [(b + 0x06, 'zp'), (b + 0x0A, 'acc'), (b + 0x0E, 'abs'), (b + 0x16, 'zpx'), (b + 0x1E, 'abx')])
_g('inc', [(0xE6, 'zp'), (0xEE, 'abs'), (0xF6, 'zpx'), (0xFE, 'abx')])
_g('dec', [(0xC6, 'zp'), (0xCE, 'abs'), (0xD6, 'zpx'), (0xDE, 'abx')])
_g('ldx', [(0xA2, 'imm'), (0xA6, 'zp'), (0xAE, 'abs'), (0xB6, 'zpy'), (0xBE, 'aby')])
_g('ldy', [(0xA0, 'imm'), (0xA4, 'zp'), (0xAC, 'abs'), (0xB4, 'zpx'), (0xBC, 'abx')])
_g('stx', [(0x86, 'zp'), (0x8E, 'abs'), (0x96, 'zpy')])
_g('sty', [(0x84, 'zp'), (0x8C, 'abs'), (0x94, 'zpx')])
_g('cpx', [(0xE0, 'imm'), (0xE4, 'zp'), (0xEC, 'abs')])
_g('cpy', [(0xC0, 'imm'), (0xC4, 'zp'), (0xCC, 'abs')])
_g('bit', [(0x24, 'zp'), (0x2C, 'abs')])
_g('jmp', [(0x4C, 'abs'), (0x6C, 'ind')])
_g('jsr', [(0x20, 'abs')])
for op, mn in ((0x10, 'bpl'), (0x30, 'bmi'), (0x50, 'bvc'), (0x70, 'bvs'), (0x90, 'bcc'),
               (0xB0, 'bcs'), (0xD0, 'bne'), (0xF0, 'beq')):
    OPC[op] = (mn, 'rel')
for op, mn in ((0x00, 'brk'), (0x08, 'php'), (0x18, 'clc'), (0x28, 'plp'), (0x38, 'sec'),
               (0x40, 'rti'), (0x48, 'pha'), (0x58, 'cli'), (0x60, 'rts'), (0x68, 'pla'),
               (0x78, 'sei'), (0x88, 'dey'), (0x8A, 'txa'), (0x98, 'tya'), (0x9A, 'txs'),
               (0xA8, 'tay'), (0xAA, 'tax'), (0xB8, 'clv'), (0xBA, 'tsx'), (0xC8, 'iny'),
               (0xCA, 'dex'), (0xD8, 'cld'), (0xE8, 'inx'), (0xEA, 'nop'), (0xF8, 'sed')):
    OPC[op] = (mn, 'imp')
SIZE = {'imp': 1, 'acc': 1, 'imm': 2, 'zp': 2, 'zpx': 2, 'zpy': 2, 'izx': 2, 'izy': 2,
        'rel': 2, 'abs': 3, 'abx': 3, 'aby': 3, 'ind': 3}
BRANCH = {'bpl', 'bmi', 'bvc', 'bvs', 'bcc', 'bcs', 'bne', 'beq'}

# base cycles (no page-cross / branch-taken extras)
def cycles(mn, mode):
    if mn in BRANCH:
        return 2
    if mn in ('sta', 'stx', 'sty'):
        return {'zp': 3, 'zpx': 4, 'zpy': 4, 'abs': 4, 'abx': 5, 'aby': 5, 'izx': 6, 'izy': 6}[mode]
    if mn in ('asl', 'lsr', 'rol', 'ror', 'inc', 'dec'):
        return {'acc': 2, 'zp': 5, 'zpx': 6, 'abs': 6, 'abx': 7}[mode]
    if mn == 'jmp':
        return 3 if mode == 'abs' else 5
    if mn in ('jsr', 'rts', 'rti'):
        return 6
    if mn in ('pha', 'php'):
        return 3
    if mn in ('pla', 'plp'):
        return 4
    if mn == 'brk':
        return 7
    if mode in ('imp', 'acc'):
        return 2
    return {'imm': 2, 'zp': 3, 'zpx': 4, 'zpy': 4, 'abs': 4, 'abx': 4, 'aby': 4, 'izx': 6, 'izy': 5}[mode]


Ins = namedtuple('Ins', 'addr size op mn mode arg src file line labels macro data idx')

_LINE = re.compile(r'^\s*(\d+) (.*)$')
_HEX4 = re.compile(r'^[0-9A-F]{4}$')
_HEX2 = re.compile(r'^[0-9A-F]{2}$')
_DATA = re.compile(r'^\s*(?:dta|\.byte|\.by|\.he|\.wo|\.word|\.db|\.dw|ins|\.ds|\.long|\.dword|\.sb|\.cb|:)', re.I)


def _resolve(base, build):
    order = ('src_game', 'src') if build == 'game' else ('src', 'src_game')
    for d in order:
        p = os.path.join(d, base)
        if os.path.exists(os.path.join(PROJ, p)):
            return p.replace('\\', '/')
    return base


_FL = {}
def _file_lines(path):
    if path not in _FL:
        try:
            _FL[path] = open(os.path.join(PROJ, path), encoding='latin-1').read().splitlines()
        except OSError:
            _FL[path] = None
    return _FL[path]


def read(build='game', lst=None):
    """-> list of Ins in listing order (code AND data lines; data has data=True)."""
    if lst is None:
        lst = os.path.join(PROJ, 'out', 'awgame.lst' if build == 'game' else 'awintro.lst')
    out = []
    stack = []                   # [file, last line number] -- MADS announces an include
    in_macro = False             #   when it ENTERS it, never when it ends: a file resumes
    pending = []                 #   where a line number is exactly its last+1
    for raw in open(lst, encoding='latin-1'):
        raw = raw.rstrip(EOL)
        if raw.startswith('Source: '):
            name = _resolve(raw[8:].strip(), build)
            idx = next((k for k in range(len(stack) - 1, -1, -1) if stack[k][0] == name), None)
            if idx is None:
                stack.append([name, 0])
            else:
                del stack[idx + 1:]
            in_macro = False
            continue
        if raw.startswith('Macro: '):
            in_macro = True
            continue
        m = _LINE.match(raw)
        if not m or not stack:
            continue
        lno = int(m.group(1))
        rest = m.group(2)
        eq = rest.startswith('= ')
        if TAB in rest:
            head, src = rest.split(TAB, 1)
            src = src.lstrip(TAB)
        else:
            head, src = rest, ''
        if ' + ' in head:                       # data continuation marker
            head, tail = head.split(' + ', 1)
            src = tail + src
        if not in_macro:
            # which open file does this line belong to? line-number continuity first;
            # when that is ambiguous or broken, the open file whose line `lno` IS this
            # text (blank / comment-only lines never decide a switch)
            txt = src.strip()
            def _is(k):
                fs = _file_lines(stack[k][0])
                return fs is not None and 0 < lno <= len(fs) and fs[lno - 1].strip() == txt
            top = len(stack) - 1
            decisive = txt != '' and not txt.startswith(';')
            if lno != stack[top][1] + 1 or (decisive and not _is(top)):
                idx = None
                if decisive:
                    idx = next((k for k in range(top, -1, -1) if _is(k)), None)
                if idx is None:
                    idx = next((k for k in range(top, -1, -1) if stack[k][1] + 1 == lno), None)
                if idx is not None:
                    del stack[idx + 1:]
            stack[-1][1] = lno
        fl = (stack[-1][0], stack[-1][1] if in_macro else lno)
        if eq:
            continue
        toks = head.split()
        addr = None
        if toks and _HEX4.match(toks[0]):
            addr = int(toks[0], 16)
            toks = toks[1:]
        byts = [int(t, 16) for t in toks if _HEX2.match(t)]
        code = src.split(';', 1)[0].rstrip()
        # label in column 0 of the source text
        label = None
        body = code
        if code and not code[0].isspace():
            first = code.split(None, 1)
            if first[0].lower() == '.proc' and len(first) > 1:
                label = first[1].split()[0]
                body = ''
            elif not first[0].startswith('.'):
                label = first[0]
                body = first[1] if len(first) > 1 else ''
        else:
            b2 = code.strip().split(None, 1)
            if b2 and b2[0].lower() == '.proc' and len(b2) > 1:
                label = b2[1].split()[0]
                body = ''
        if addr is None:
            continue
        if label and not (body.strip().lower().startswith(('equ', '=', '.endp'))):
            pending.append(label)
        if not byts:
            continue
        is_data = bool(_DATA.match(body)) or body.strip().lower().startswith(('.byte', '.he', 'dta'))
        if is_data:
            out.append(Ins(addr, len(byts), None, None, None, None, src.strip(), fl[0], fl[1],
                           tuple(pending), in_macro, True, len(out)))
            pending = []
            continue
        # decode the emitted bytes (may be >1 instruction: jeq/jne expansions)
        p = 0
        while p < len(byts):
            op = byts[p]
            if op not in OPC:
                out.append(Ins(addr + p, len(byts) - p, op, None, None, None, src.strip(), fl[0], fl[1],
                               tuple(pending), in_macro, True, len(out)))
                pending = []
                break
            mn, mode = OPC[op]
            n = SIZE[mode]
            if p + n > len(byts):
                out.append(Ins(addr + p, len(byts) - p, op, None, None, None, src.strip(), fl[0], fl[1],
                               tuple(pending), in_macro, True, len(out)))
                pending = []
                break
            arg = None
            if n == 2:
                arg = byts[p + 1]
                if mode == 'rel':
                    off = arg - 256 if arg >= 128 else arg
                    arg = (addr + p + 2 + off) & 0xFFFF
            elif n == 3:
                arg = byts[p + 1] | (byts[p + 2] << 8)
            out.append(Ins(addr + p, n, op, mn, mode, arg, src.strip(), fl[0], fl[1],
                           tuple(pending), in_macro, False, len(out)))
            pending = []
            p += n
    return out


def labels(build='game', lst=None):
    """name -> address, from the listing's label definitions (same set _cpu6502.labels
    reads, plus .proc names)."""
    res = {}
    for i in read(build, lst):
        for nm in i.labels:
            res.setdefault(nm, i.addr)
    return res
