#!/usr/bin/env python3
"""find_overflow.py - locate 6502 branch-out-of-range errors and show WHERE each
branch binds, so MADS '?'-local-label mis-bindings are obvious.

MADS only says "Branch out of range by $NNNN bytes" at file(line). This:
  1. assembles awgame (or the file you pass) with a listing,
  2. for every such error, reads the offending source line to get the target label,
  3. finds EVERY definition of that label in the listing with its address,
  4. prints the branch address + each candidate target + signed distance, flagging the
     ones outside [-128,+127]. A '?'-local that binds to a FAR same-named label (instead
     of the nearby one) is the classic cause -> the fix is a unique label name.

Run:   python tools/find_overflow.py                     # builds src_game/awgame.asm
       python tools/find_overflow.py src/awvbxe.asm
"""
import os, re, sys, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
MADS = os.path.join(PROJ, 'mads.exe')
BRANCHES = {'bcc', 'bcs', 'beq', 'bne', 'bmi', 'bpl', 'bvc', 'bvs', 'bra'}


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else 'src_game/awgame.asm'
    lst = os.path.join(PROJ, 'out', 'overflow.lst')
    os.makedirs(os.path.join(PROJ, 'out'), exist_ok=True)
    r = subprocess.run([MADS, src, '-o:' + os.path.join(PROJ, 'out', 'overflow.xex'),
                        '-l:' + lst], cwd=PROJ, capture_output=True, text=True)
    out = r.stdout + r.stderr
    errs = re.findall(r'(\S+) \((\d+)\) ERROR: Branch out of range by \$([0-9A-Fa-f]+)', out)
    if not errs:
        print('No branch-out-of-range errors.' if 'ERROR' not in out
              else 'Other errors:\n' + '\n'.join(l for l in out.splitlines() if 'ERROR' in l))
        return

    # index the listing: src-line -> address ; and label -> [(address, src-line)]
    lst_lines = open(lst, encoding='latin-1').read().splitlines() if os.path.exists(lst) else []
    line_addr = {}                         # listing 'srcno' -> hex address
    label_defs = {}                        # label -> [(addr, srcno)]
    lab_re = re.compile(r'^\s*(\d+)\s+([0-9A-Fa-f]{4})\b(.*)$')
    deflab_re = re.compile(r'([?@A-Za-z_][\w?]*)')
    for ln in lst_lines:
        m = lab_re.match(ln)
        if not m:
            continue
        srcno, addr, rest = int(m.group(1)), m.group(2), m.group(3)
        line_addr[srcno] = addr
        # a label definition: token at the very start of the source text (after the hex+bytes)
        s = rest
        # strip leading hex opcode bytes (e.g. " F0 63 ")
        s2 = re.sub(r'^(\s+[0-9A-Fa-f]{2})+\s', ' ', s)
        dm = re.match(r'\s*([?@A-Za-z_][\w?]*)\b', s2)
        if dm and dm.group(1).lower() not in BRANCHES:
            label_defs.setdefault(dm.group(1), []).append((int(addr, 16), srcno))

    def resolve(fname):                    # error file is a basename of an icl'd file
        for d in ('src_game', 'src', '.'):
            p = os.path.join(PROJ, d, os.path.basename(fname))
            if os.path.exists(p):
                return p
        return None

    print(f'== {len(errs)} branch-out-of-range error(s) in {src} ==\n')
    for f, line, over in errs:
        line = int(line)
        p = resolve(f)
        srctext = open(p, encoding='latin-1').read().splitlines() if p else []
        srcline = srctext[line - 1] if 0 < line <= len(srctext) else '???'
        bm = re.search(r'\b(' + '|'.join(BRANCHES) + r')\s+([?@A-Za-z_][\w?]*)', srcline.lower())
        target = bm.group(2) if bm else '?'
        # branch address: find the listing entry whose source line == this line in this file
        # (listing srcno is global; match by the source text appearing in the listing)
        baddr = None
        for ln in lst_lines:
            if srcline.strip() and srcline.strip() in ln:
                am = lab_re.match(ln)
                if am:
                    baddr = int(am.group(2), 16); break
        print(f'{f}:{line}  overflow=${over}   `{srcline.strip()}`')
        print(f'    branch -> label "{target}"' + (f'  (branch @ ${baddr:04X})' if baddr else ''))
        defs = label_defs.get(target, [])
        if not defs:
            # try without leading ? (MADS sometimes lists locals differently)
            defs = label_defs.get(target.lstrip('?'), [])
        for addr, srcno in defs:
            dist = (addr - (baddr + 2)) if baddr is not None else None
            flag = '' if dist is None else ('  <-- in range' if -128 <= dist <= 127 else f'  <-- OUT OF RANGE ({dist:+d})')
            print(f'      def @ ${addr:04X} (lst line {srcno}){flag}')
        print()


if __name__ == '__main__':
    main()
