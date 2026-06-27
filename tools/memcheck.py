#!/usr/bin/env python3
"""
memcheck.py - does anything overflow / collide in memory?

Checks the awvbxe build against the Atari XL/XE + VBXE memory map this port
relies on (see docs/PORT.md):

  $0000-$00FF  zero page   (we use $80-$D3 ; $00-$7F is the OS)
  $0100-$01FF  CPU stack
  $0600-$06FF  loader bank-switch stub (transient, during load only)
  $2000-$3FFF  our CODE  -- MUST end below $4000
  $4000-$7FFF  MEMAC-B window (VRAM data; segments here STREAM into VRAM)
  $8000-$8FFF  MEMAC-A window (control bank; not runtime RAM)
  $9000-$BFFF  free RAM  -- our big data tables + work RAM live here
  $C000-$FFFF  OS ROM ($D000-$D7FF = hardware)

Two things are inspected:
  1. the .xex segments (what the loader actually places in memory), and
  2. the ZP / work-RAM equates parsed from the .asm (uninitialised, so not in
     the .xex) -- to catch ZP running past $D3 or the work area hitting ROM.

Usage:
    python tools/memcheck.py [src/awvbxe.asm] [awintro.xex]

Exits non-zero if any ERROR is found, so it can gate a build.
"""
import os, re, sys, struct

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

# --- window / region constants (overridable from the asm if the symbols exist)
DEF = dict(CODE_LO=0x2000, MEMACB=0x4000, MEMACB_END=0x8000,
           MEMACA=0x8000, MEMACA_END=0x9000, DATA_LO=0x9000,
           ROM=0xC000, HW_LO=0xD000, HW_HI=0xD800, ZP_LIMIT=0xD3)


# ---------------------------------------------------------------------------
# 1. parse asm equates  (LABEL equ EXPR  /  LABEL = EXPR)
# ---------------------------------------------------------------------------
def iter_asm_lines(asm_path, _seen=None):
    """Yield lines of asm_path, expanding `icl 'path'` includes recursively (the
    source is split into src/aw_*.asm modules). Include paths are resolved
    relative to the project root, the same as when mads is run from there."""
    if _seen is None:
        _seen = set()
    ap = os.path.abspath(asm_path)
    if ap in _seen:
        return
    _seen.add(ap)
    icl = re.compile(r"^\s*icl\s+'([^']+)'", re.IGNORECASE)
    for line in open(asm_path, encoding='ascii', errors='replace'):
        m = icl.match(line)
        if m:
            # Follow our own split-out modules, but NOT vbxe.inc -- that is the
            # third-party hardware register file (VBXE_* at $D6xx, constants like
            # PRI_ALL=$FF) and parsing it would flag those as bogus ZP equates.
            if os.path.basename(m.group(1)).lower() != 'vbxe.inc':
                inc = os.path.join(PROJ, m.group(1))
                if os.path.exists(inc):
                    yield from iter_asm_lines(inc, _seen)
            continue
        yield line


def parse_equates(asm_path):
    raw = {}
    pat = re.compile(r'^\s*(\w+)\s+(?:equ|=)\s+([^;]+?)\s*(?:;.*)?$',
                     re.IGNORECASE)
    for line in iter_asm_lines(asm_path):
        m = pat.match(line)
        if not m:
            continue
        name, expr = m.group(1), m.group(2).strip()
        raw[name] = expr
    return raw


def resolve(raw):
    """Resolve as many equates as possible to ints (iterative, dependency-order)."""
    vals = {}

    def evalexpr(expr):
        e = expr.replace('[', '(').replace(']', ')')
        # mads hex $xx -> 0xXX ; binary %.. -> int ; integer divide
        e = re.sub(r'\$([0-9A-Fa-f]+)', lambda m: str(int(m.group(1), 16)), e)
        e = re.sub(r'%([01]+)', lambda m: str(int(m.group(1), 2)), e)
        e = e.replace('/', '//')
        # leftover identifiers must already be in vals
        for tok in set(re.findall(r'[A-Za-z_]\w*', e)):
            if tok not in vals:
                raise KeyError(tok)
            e = re.sub(r'\b' + tok + r'\b', str(vals[tok]), e)
        return eval(e, {'__builtins__': {}}, {})

    for _ in range(40):
        progressed = False
        for name, expr in raw.items():
            if name in vals:
                continue
            try:
                vals[name] = int(evalexpr(expr))
                progressed = True
            except Exception:
                pass
        if not progressed:
            break
    return vals


# ---------------------------------------------------------------------------
# 2. parse the .xex segments
# ---------------------------------------------------------------------------
def parse_xex(path):
    d = open(path, 'rb').read()
    segs = []
    i = 0
    # leading $FFFF
    if d[0:2] == b'\xff\xff':
        i = 2
    while i + 4 <= len(d):
        # optional repeated $FFFF between segments
        while d[i:i+2] == b'\xff\xff':
            i += 2
        if i + 4 > len(d):
            break
        start, end = struct.unpack_from('<HH', d, i)
        i += 4
        n = end - start + 1
        if n < 0 or i + n > len(d):
            segs.append((start, end, n, True))     # malformed
            break
        segs.append((start, end, n, False))
        i += n
    return segs


SPECIAL = {0x02E0: 'RUNAD', 0x02E2: 'INITAD'}


def classify(start, end, M):
    """Return (tag, level) where level in {ok, info, warn, error}."""
    if start in SPECIAL or (start == 0x02E0):
        return (SPECIAL.get(start, 'vector'), 'info')
    if start == 0x02E2:
        return ('INITAD', 'info')
    # loader stub page
    if 0x0600 <= start <= 0x06FF and end <= 0x06FF:
        return ('loader stub (transient)', 'info')
    # VRAM stream into the MEMAC-B window
    if start == M['MEMACB']:
        if end < M['MEMACB_END']:
            return ('VRAM stream -> MEMAC-B (transient)', 'info')
        return ('MEMAC-B chunk OVERRUNS the 16K window', 'error')
    # code
    if M['CODE_LO'] <= start < M['MEMACB']:
        if end >= M['MEMACB']:
            return ('CODE crosses $4000 into the MEMAC-B window', 'error')
        return ('code', 'ok')
    # inside a window but not at its base -> would be hidden at runtime
    if M['MEMACB'] < start < M['MEMACB_END']:
        return ('segment INSIDE the MEMAC-B window (hidden at runtime)', 'error')
    if M['MEMACA'] <= start < M['MEMACA_END']:
        return ('segment INSIDE the MEMAC-A window (hidden at runtime)', 'error')
    # data RAM
    if M['DATA_LO'] <= start < M['ROM']:
        if end >= M['ROM']:
            return ('data crosses $C000 into OS ROM', 'error')
        return ('data', 'ok')
    # ROM / hardware
    if start >= M['ROM']:
        if M['HW_LO'] <= start < M['HW_HI']:
            return ('writes into the $D000 hardware area', 'error')
        return ('overwrites OS ROM', 'warn')
    return ('low RAM', 'ok')


def main():
    asm = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, 'src', 'awvbxe.asm')
    xex = sys.argv[2] if len(sys.argv) > 2 else os.path.join(PROJ, 'awintro.xex')

    M = dict(DEF)
    errors = warns = 0

    # pull overridable window symbols from the asm if present
    syms = {}
    if os.path.exists(asm):
        syms = resolve(parse_equates(asm))
        for k, sym in (('MEMACB', 'DATAW'), ('MEMACA', 'MEMW'),
                       ('CODE_LO', None), ('DATA_LO', None)):
            if sym and sym in syms:
                M[k] = syms[sym]

    print('=' * 68)
    print(' MEMORY CHECK  ', os.path.relpath(xex, PROJ))
    print('=' * 68)

    # --- xex segments ---
    if not os.path.exists(xex):
        print('  (no .xex; build first)')
        return 1
    segs = parse_xex(xex)
    persistent = []   # (start,end) for overlap test (code + data only)
    print('\n .xex segments:')
    for start, end, n, bad in segs:
        if bad:
            print(f'   ${start:04X}-${end:04X}  MALFORMED segment')
            errors += 1
            continue
        tag, lvl = classify(start, end, M)
        mark = {'ok': '  ', 'info': 'i ', 'warn': '! ', 'error': 'XX'}[lvl]
        print(f'   {mark} ${start:04X}-${end:04X} ({n:5}B)  {tag}')
        if lvl == 'error':
            errors += 1
        elif lvl == 'warn':
            warns += 1
        if tag in ('code', 'data'):
            persistent.append((start, end, tag))

    # --- overlap among persistent RAM segments ---
    persistent.sort()
    for i in range(len(persistent) - 1):
        s0, e0, t0 = persistent[i]
        s1, e1, t1 = persistent[i + 1]
        if s1 <= e0:
            print(f'   XX OVERLAP ${s1:04X}-${e0:04X} between {t0} and {t1}')
            errors += 1

    # --- ZP + work RAM from equates ---
    if syms:
        print('\n zero page (want $80-${:02X}):'.format(M['ZP_LIMIT']))
        zp = {k: v for k, v in syms.items() if 0x80 <= v <= 0xFF}
        if zp:
            hi = max(zp.values())
            hk = [k for k, v in zp.items() if v == hi][0]
            print(f'   highest ZP equate: ${hi:02X} ({hk})')
            over = {k: v for k, v in zp.items() if v > M['ZP_LIMIT']}
            if over:
                for k, v in sorted(over.items(), key=lambda kv: kv[1]):
                    print(f'   ! ZP ${v:02X} {k} is above the $D3 working limit')
                warns += len(over)

        # work-RAM block (RAMB .. pstk+256)
        if 'RAMB' in syms:
            top = syms.get('pstk', syms['RAMB']) + 256
            base = syms['RAMB']
            print(f'\n work RAM: ${base:04X}-${top-1:04X} ({top-base}B)')
            if top > M['ROM']:
                print('   XX work RAM runs into OS ROM ($C000)')
                errors += 1
            elif not (M['DATA_LO'] <= base and top <= M['ROM']):
                print('   ! work RAM outside the $9000-$BFFF free area')
                warns += 1
            else:
                print('   ok (inside $9000-$BFFF free RAM)')
            # collision with loaded data segments
            for s, e, t in persistent:
                if t == 'data' and not (top <= s or base > e):
                    print(f'   XX work RAM overlaps data segment ${s:04X}-${e:04X}')
                    errors += 1

    print('\n' + '-' * 68)
    print(f' result: {errors} error(s), {warns} warning(s)  ->',
          'FAIL' if errors else 'PASS')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
