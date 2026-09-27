"""ifelse_merge.py - turn every difference between the reconstructed ORIGINAL sources
(_zal/orig_20260909) and the CURRENT sources into drac030-style blocks:

    .if 1
        <new code>
    .else
        <original code, verbatim>
    .endif

Hunks inside one .proc are coalesced into a single block covering the whole proc (both
sides stay complete routines, label/if nesting cannot be split); .macro bodies are NOT
expanded (MADS wants the .macro/.endm pair outside conditionals) -- those get a line-level
block inside the body. Hunks in plain areas are coalesced when closer than GAP lines.
Comment-only hunks are left alone. Existing wrappers are stripped first, so the script
is idempotent.
"""
import os, sys, difflib, re
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIG = os.path.join(PROJ, '_zal', 'orig_20260909')
GAP = 4
FILES = ['src/aw_raster.asm', 'src/aw_polygon.asm', 'src/aw_vbxe.asm', 'src/aw_equates.inc',
         'src_game/aw_raster.asm', 'src_game/aw_polygon.asm', 'src_game/game_zp.inc',
         'src_game/game_vm_ops1.asm', 'src_game/game_vm_ops2.asm', 'src_game/game_cellcache.asm',
         'src_game/game_text.asm', 'src_game/game_vm_draw.asm', 'src_game/game_vm_ops3.asm']


def code_only(lines):
    out = []
    for l in lines:
        c = l.split(';')[0].rstrip()
        if c.strip():
            out.append(re.sub(r'\s+', ' ', c.strip()))
    return out


def block_ranges(lines):
    """[(start, end)] inclusive line ranges of .proc/.endp blocks."""
    ranges = []; start = None
    for i, l in enumerate(lines):
        t = l.strip().lower()
        if t.startswith('.proc'):
            start = i
        elif t.startswith('.endp') and start is not None:
            ranges.append((start, i)); start = None
    return ranges


def unwrap(lines):
    """strip existing top-level .if 1/.else/.endif wrappers (keep the .if 1 side)."""
    out = []; depth = 0; skip = False; inner = 0
    for l in lines:
        t = l.strip().lower()
        if l == '.if 1' and depth == 0:
            depth = 1; inner = 0; continue
        if depth == 1:
            if t.startswith(('.if', '.ifdef', '.ifndef')):
                inner += 1
            elif t.startswith('.endif'):
                if inner:
                    inner -= 1
                else:
                    depth = 0; skip = False; continue
            elif t == '.else' and inner == 0:
                skip = True; continue
        if not skip:
            out.append(l)
    return out


def merge(orig_lines, new_lines):
    sm = difflib.SequenceMatcher(None, orig_lines, new_lines, autojunk=False)
    hunks = [(t, i1, i2, j1, j2) for t, i1, i2, j1, j2 in sm.get_opcodes() if t != 'equal']
    blocks = block_ranges(new_lines)

    def blk(j):
        for s, e in blocks:
            if s <= j <= e:
                return (s, e)
        return None

    groups = []
    for h in hunks:
        t, i1, i2, j1, j2 = h
        if groups:
            g = groups[-1]
            gj2 = g[-1][4]
            same_blk = blk(j1) is not None and blk(j1) == blk(g[0][3])
            macro_between = any(l.strip().lower().startswith(('.macro', '.endm')) for l in new_lines[gj2:j1])
            if (same_blk or j1 - gj2 <= GAP) and not macro_between:
                g.append(h); continue
        groups.append([h])
    ranges = []
    for g in groups:
        i1, j1 = g[0][1], g[0][3]
        i2, j2 = g[-1][2], g[-1][4]
        b = blk(j1) if blk(j1) is not None and blk(j1) == blk(max(j2 - 1, j1)) else None
        if b is not None:
            s, e = b
            head = new_lines[s].strip()
            os_ = next((k for k, l in enumerate(orig_lines) if l.strip() == head), None)
            if os_ is not None:
                oe = next(k for k in range(os_, len(orig_lines)) if orig_lines[k].strip().lower().startswith('.endp'))
                if i1 >= os_ and i2 - 1 <= oe:
                    i1, i2, j1, j2 = os_, oe + 1, s, e + 1
        ranges.append([i1, i2, j1, j2])
    ranges.sort(key=lambda r: r[2])
    merged_r = []
    for r in ranges:
        if merged_r and (r[2] <= merged_r[-1][3] or r[0] <= merged_r[-1][1]):
            m = merged_r[-1]
            m[0] = min(m[0], r[0]); m[1] = max(m[1], r[1]); m[2] = min(m[2], r[2]); m[3] = max(m[3], r[3])
        else:
            merged_r.append(r)
    out = []; pos_new = 0; n_wrapped = 0
    for i1, i2, j1, j2 in merged_r:
        new_part = new_lines[j1:j2]; old_part = orig_lines[i1:i2]
        if code_only(new_part) == code_only(old_part):
            out.extend(new_lines[pos_new:j2]); pos_new = j2
            continue
        out.extend(new_lines[pos_new:j1])
        out.append('.if 1')
        out.extend(new_part)
        out.append('.else')
        out.extend(old_part)
        out.append('.endif')
        pos_new = j2; n_wrapped += 1
    out.extend(new_lines[pos_new:])
    return out, n_wrapped


def main():
    for rel in FILES:
        op = os.path.join(ORIG, rel.replace('/', os.sep)); np = os.path.join(PROJ, rel.replace('/', os.sep))
        orig = open(op, encoding='utf-8').read().split('\n')
        new = unwrap(open(np, encoding='utf-8').read().split('\n'))
        if orig == new:
            print('%-32s unchanged' % rel); continue
        merged, n = merge(orig, new)
        assert unwrap(merged) == new, rel
        open(np, 'w', encoding='utf-8', newline='\n').write('\n'.join(merged))
        print('%-32s %d .if 1/.else/.endif block(s)' % (rel, n))


if __name__ == '__main__':
    main()
