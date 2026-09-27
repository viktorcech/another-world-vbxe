#!/usr/bin/env python3
"""_stub_fetch -- 6502-idioms "rare case out of line, not-taken branch" for the byte-fetch
macros (mfetch / mfetch0 / m_pfetch / m_vm_w / m_vm_w0) of the GAME.

Each macro ends `inc ptr / bne *+5 / jsr wrap`: the common case (no page wrap) takes the
branch (3 cycles). The x-variants end `inc ptr / beq stub` (2 cycles) and the rare wrap
runs `stub: jsr wrap / jmp back`, placed after the next unconditional exit (jmp / rts)
in the same block. Flags and registers are the same on both paths (the not-taken beq
leaves the inc's flags, the stub returns with pl/pf_wrap's own, as the jsr did).

Every executed invocation (profile: out/bench/prof_all_cnt.json) is converted, most
executed first, while the byte budget lasts (+3 bytes a site); the originals stay in
the .else of an .if 1 block (skill_verify's old build). Sites the assembler rejects
(branch out of range) are put back automatically.

    python tools/_stub_fetch.py [budget bytes, default 150] [--region-file F:budget ...]
"""
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _lst6502 as LS                                             # noqa: E402

MAC = {'mfetch0': ('mfetch0x', 'pl_wrap'), 'mfetch': ('mfetchx', 'pl_wrap'),
       'm_pfetch': ('m_pfetchx', 'pf_wrap')}
PAIR = {'m_vm_w0': ('mfetch0', 'mfetch0'), 'm_vm_w': ('mfetch', 'mfetch0')}
INV = re.compile(r'^(\s+)(mfetch0|mfetch|m_pfetch|m_vm_w0|m_vm_w)\b(.*)$')
EXIT = re.compile(r'^(?:[\w?@.]+)?\s+(jmp|rts|rti)\b', re.I)


def counts():
    cnt = json.load(open(os.path.join(PROJ, 'out', 'bench', 'prof_all_cnt.json')))
    per = defaultdict(float)
    for i in LS.read('game'):
        if i.mn == 'bne' and i.src and '*+5' in i.src:
            per[(os.path.normpath(i.file), i.line)] += cnt.get(str(i.addr), 0)
    return per


def assemble():
    r = subprocess.run([os.path.join(PROJ, 'mads.exe'), 'src_game/awgame.asm', '-d:GAME_SEC=2941',
                        '-o:out/_g.xex', '-l:out/_g.lst'], cwd=PROJ, capture_output=True, text=True)
    err = [ln for ln in r.stdout.splitlines() if 'ERROR' in ln]
    if not err:
        # mads has no guard on the $2000 chain's end: a segment sliding past $4000
        # (the MEMAC-B window) assembles fine and dies at runtime -- gate it here.
        b = subprocess.run([sys.executable, os.path.join(HERE, 'check_xex.py'), 'out/_g.xex'],
                           cwd=PROJ, capture_output=True, text=True)
        if b.returncode != 0:
            err = ['out/_g.xex (0) ' + ln for ln in b.stdout.splitlines() if 'overlaps' in ln]
    return err


def place(lines, k):
    """index of the exit line after which a stub for the invocation at k may go."""
    depth = 0
    for j in range(k + 1, min(len(lines), k + 80)):
        t = lines[j].split(';')[0]
        st = t.strip().lower()
        if st.startswith('.if'):
            depth += 1
        elif st.startswith('.else') or st.startswith('.elseif'):
            if depth == 0:
                return None
        elif st.startswith('.endif'):
            if depth == 0:
                return None
            depth -= 1
        elif st.startswith('.endp') or st.startswith('.proc') or st.startswith('.macro') \
                or st.startswith('.endm'):
            return None
        elif depth == 0 and EXIT.match(t):
            return j
    return None


def addr_map():
    m = {}
    for i in LS.read('game'):
        if i.mn and not i.data:
            key = (os.path.normpath(i.file), i.line)
            m.setdefault(key, i.addr)
    return m


def main():
    amap = addr_map()
    budget = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 150
    per = counts()
    srcs = {}
    sites = []
    for fn in sorted(os.listdir(os.path.join(PROJ, 'src_game'))):
        if not fn.endswith('.asm'):
            continue
        p = os.path.join('src_game', fn)
        lines = open(os.path.join(PROJ, p), encoding='latin-1').read().split('\n')
        srcs[p] = lines
        for k, ln in enumerate(lines):
            m = INV.match(ln)
            if not m or '.macro' in ln:
                continue
            c = per.get((os.path.normpath(os.path.join(PROJ, p)), k + 1), 0) or \
                per.get((os.path.normpath(p), k + 1), 0)
            sites.append((c, p, k, m.group(2)))     # c == 0 too: every site counts
    sites.sort(key=lambda s: -s[0])
    chosen, used = [], 0
    for c, p, k, mac in sites:
        cost = 6 if mac in PAIR else 3
        if used + cost > budget:
            continue
        ex = place(srcs[p], k)
        if ex is None:
            continue
        a0 = amap.get((os.path.normpath(p), k + 1))
        a1 = amap.get((os.path.normpath(p), ex + 1))
        if a0 is None or a1 is None or a1 - a0 > 100:
            continue                               # the stub would sit out of branch range
        chosen.append((c, p, k, mac))
        used += cost
    orig = {p: list(v) for p, v in srcs.items()}
    base = 0                                       # continue after stubs from earlier runs
    for lines in orig.values():
        for ln in lines:
            for mm in re.finditer(r'\bsf[wr](\d+)\b', ln, re.I):
                base = max(base, int(mm.group(1)))
    reject = set()
    for attempt in range(40):
        work = {p: list(v) for p, v in orig.items()}
        stubs = defaultdict(list)                  # (file, exit index) -> stub lines
        repl = {}
        n = base
        for c, p, k, mac in chosen:
            if (p, k) in reject:
                continue
            ex = place(work[p], k)
            ind = INV.match(work[p][k]).group(1)
            tail = INV.match(work[p][k]).group(3)
            parts = PAIR.get(mac, (mac,))
            new = ['.if 1']
            for q, sub in enumerate(parts):
                n += 1
                xm, wr = MAC[sub]
                lw, lr = 'sfw%d' % n, 'sfr%d' % n
                new.append('%s%s %s%s' % (ind, xm, lw, tail if q == 0 else ''))
                new.append(lr)
                if mac in PAIR:
                    new.append('%ssta vm_s%d' % (ind, 2 if q == 0 else 1))
                stubs[(p, ex)] += ['%-8sjsr %s' % (lw, wr), '        jmp %s' % lr]
            new += ['.else', work[p][k], '.endif']
            repl[(p, k)] = new
        newline = {}                               # (file, new line no) -> original index
        for p in work:
            out = []
            for k, ln in enumerate(work[p]):
                add = repl.get((p, k), [ln])
                if (p, k) in stubs:
                    add = add + ['.if 1'] + stubs[(p, k)] + ['.endif']
                for x in add:
                    out.append(x)
                    newline[(p, len(out))] = k
            open(os.path.join(PROJ, p), 'w', encoding='latin-1').write('\n'.join(out))
        err = assemble()
        if not err:
            print('converted %d sites (%d bytes), rejected %d' % (len(chosen) - len(reject), used, len(reject)))
            return
        if err[0].startswith('out/_g.xex'):
            live = [s for s in chosen if (s[1], s[2]) not in reject]
            if not live:
                break
            s = min(live, key=lambda s: s[0])      # chain overflow: drop the least-
            reject.add((s[1], s[2]))               #   executed site and try again
            continue
        # an error (a branch pushed out of range, an ert layout guard) at a line: put
        # back every site whose invocation or stub sits within 80 lines before it
        hit = False
        for e in err:
            mm = re.match(r'\s*(\S+) \((\d+)\)', e)
            if not mm:
                continue
            p = os.path.join('src_game', os.path.basename(mm.group(1)))
            k0 = newline.get((p, int(mm.group(2))))
            if k0 is None:
                continue
            local = False
            for c, pp, k, mac in chosen:
                if pp != p or (pp, k) in reject:
                    continue
                ex = place(orig[pp], k)
                if k0 - 80 <= k <= k0 + 5 or (ex is not None and k0 - 80 <= ex <= k0 + 5):
                    reject.add((pp, k))
                    hit = local = True
            if not local:                          # a layout guard (ert) above the code it
                for c, pp, k, mac in chosen:       #   guards: that file's sites all go back
                    if pp == p and (pp, k) not in reject:
                        reject.add((pp, k))
                        hit = True
        if not hit:
            print('\n'.join(err[:10]))
            break
    for p in orig:
        open(os.path.join(PROJ, p), 'w', encoding='latin-1').write('\n'.join(orig[p]))
    raise SystemExit('could not converge -- sources restored')


if __name__ == '__main__':
    main()
