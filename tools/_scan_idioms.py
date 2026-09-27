#!/usr/bin/env python3
"""_scan_idioms.py - find 6502 composition-rule violations in the asm sources.
Patterns (labels/comments stripped, .if/.else ignored -> hand-verify each hit):
  A: lda X / sta Y / lda Y         (reload of what A already holds)
  B: sta Y / lda Y                 (immediate reload)
  C: sta tmp ... lda tmp with no intervening write to tmp and no A clobber? (only
     the simple case: sta T / <ops not touching A> / lda T)
  D: cmp X / bcc|bcs L / sec / sbc X  (fusable carry chain)
  E: sec after bcc, clc after bcs   (dead flag set on the fall-through)
  F: bne/beq *+5 / jmp L or  bxx skip / jmp L / skip:   (-> jeq/jne style)
  G: pha/txa/pha / tya/pha register saves
"""
import re, sys, glob
A_WRITERS = {'lda','pla','txa','tya','adc','sbc','and','ora','eor','lsr','asl','rol','ror','jsr','rts','rti','jmp'}
def parse(path):
    out=[]
    for n,ln in enumerate(open(path,encoding='utf-8',errors='replace'),1):
        s=ln.split(';')[0].rstrip()
        if not s.strip(): continue
        m=re.match(r'^(\S+)?\s*(\S+)?\s*(.*)$', s)
        # label present if line starts non-space
        if not s[0].isspace():
            parts=s.split(None,1)
            label=parts[0]; rest=parts[1] if len(parts)>1 else ''
        else:
            label=None; rest=s.strip()
        if not rest: continue
        p=rest.split(None,1)
        op=p[0].lower(); arg=p[1].strip() if len(p)>1 else ''
        out.append((n,label,op,arg,ln.rstrip()))
    return out
def scan(path):
    L=parse(path); hits=[]
    for i,(n,lab,op,arg,raw) in enumerate(L):
        nxt=L[i+1] if i+1<len(L) else None
        nn=L[i+2] if i+2<len(L) else None
        # B/A
        if op=='sta' and nxt and nxt[2]=='lda' and nxt[3]==arg and nxt[1] is None:
            hits.append(('A/B reload', n, raw, nxt[4]))
        # D
        if op=='cmp' and nxt and nxt[2] in('bcc','bcs','jcc','jcs') and nn and nn[2]=='sec':
            n3=L[i+3] if i+3<len(L) else None
            if n3 and n3[2]=='sbc' and n3[3]==arg:
                hits.append(('D cmp/bcc/sec/sbc', n, raw, nxt[4], nn[4], n3[4]))
        # E
        if op in('bcc','jcc') and nxt and nxt[2]=='sec' and nxt[1] is None:
            hits.append(('E dead sec after bcc', n, raw, nxt[4]))
        if op in('bcs','jcs') and nxt and nxt[2]=='clc' and nxt[1] is None:
            hits.append(('E dead clc after bcs', n, raw, nxt[4]))
        # F
        if op in('bne','beq','bcc','bcs','bmi','bpl','bvc','bvs') and nxt and nxt[2]=='jmp' and nxt[1] is None:
            if nn and nn[1] and arg.split(',')[0]==nn[1] or arg.startswith('*+'):
                hits.append(('F branch-over-jmp', n, raw, nxt[4]))
        # G
        if op=='pha' and nxt and nxt[2] in('txa','tya') and nn and nn[2]=='pha':
            hits.append(('G reg save via stack', n, raw, nxt[4], nn[4]))
        if op in('txa','tya') and nxt and nxt[2]=='pha' and nxt[1] is None:
            hits.append(('G reg save via stack', n, raw, nxt[4]))
        # C: sta T ; k lines not touching A nor T ; lda T
        if op=='sta' and not arg.startswith('(') :
            for k in range(1,6):
                if i+k>=len(L): break
                m=L[i+k]
                if m[1] is not None: break  # label: flow may join
                if m[2] in A_WRITERS or m[2].startswith(('b','j','.','r')) or m[2] in('pha','php','plp'):
                    if m[2]=='lda' and m[3]==arg and k>1:
                        hits.append(('C store/reload', n, raw, *[x[4] for x in L[i+1:i+k+1]]))
                    break
                if m[2] in('inc','dec','asl','lsr','rol','ror','sta','stx','sty') and m[3]==arg: break
                if m[2] in('inc','dec','asl','lsr','rol','ror') and m[3]!=arg: continue
    return hits
for path in sorted(glob.glob('src/*.asm')+glob.glob('src_game/*.asm')):
    hs=scan(path)
    if hs:
        print(f'##### {path}')
        for h in hs:
            print(f'[{h[0]}] line {h[1]}'); 
            for r in h[2:]: print('    '+r)
