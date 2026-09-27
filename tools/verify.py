#!/usr/bin/env python3
"""verify.py - check everything about the GR.7 build that can be checked without
running it.  Two bugs in this port were silent (a duplicate `?ok` local label that
re-aimed a branch, and code growing into the display list), so the assembly-level
checks matter as much as the data ones.

    python gr7/verify.py
"""
import os, re, sys, struct

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
XEX = os.path.join(HERE, 'awgr7.xex')
LST = os.path.join(HERE, 'out', 'gr7.lst')

fails = []
def check(name, ok, detail=''):
    print(f"  [{'OK ' if ok else 'CHYBA'}] {name}" + (f"   {detail}" if detail else ''))
    if not ok:
        fails.append(name)


def equ(name):
    """mads: $ prefix = hex, otherwise decimal. Reading `equ 100` as hex turned
    ROWS into 256 and made this script report bogus failures."""
    for ln in open(os.path.join(HERE, 'gr7.inc'), encoding='utf-8'):
        m = re.match(rf'^{name}\s+equ\s+\$([0-9A-Fa-f]+)', ln)
        if m:
            return int(m.group(1), 16)
        m = re.match(rf'^{name}\s+equ\s+(\d+)\s*(?:;|$)', ln)
        if m:
            return int(m.group(1))
    raise KeyError(name)


def segments():
    d = open(XEX, 'rb').read()
    i, out = 2, []
    while i + 3 < len(d):
        a = d[i] | (d[i+1] << 8); e = d[i+2] | (d[i+3] << 8); i += 4
        if a == 0xFFFF:
            i -= 2; continue
        n = e - a + 1
        out.append((a, e, d[i:i+n])); i += n
    return out


print('=== 1. pamatova mapa =========================================')
segs = segments()
DLIST, ROWS, BPR = equ('DLIST'), equ('ROWS'), equ('BPR')
PAGE0, PALLUT, PALREG = equ('PAGE0'), equ('PALLUT'), equ('PALREG')
GR7TAB, RAMB, FMULB, POLYLUT = equ('GR7TAB'), equ('RAMB'), equ('FMULB'), equ('POLYLUT')

ram = sorted({(a, e) for a, e, b in segs if len(b) > 8 and not (0x4000 <= a < 0x8000)})
overlap = [(x, y) for x, y in zip(ram, ram[1:]) if y[0] <= x[1]]
check('ziadne prekryvajuce sa segmenty', not overlap, str(overlap))
check('pod $2000 len zvukovy driver $0900-$0EFF (BRIGHT je $0F00)',
      all(a >= 0x2000 or (0x0900 <= a and e < 0x0F00) for a, e in ram),
      ' '.join('$%04X-$%04X' % (a, e) for a, e in ram if a < 0x2000))
code_end = max(e for a, e in ram if a == 0x2000)
check('kod konci pod display listom', code_end < DLIST,
      'kod do $%04X, DLIST $%04X, rezerva %d B' % (code_end, DLIST, DLIST - code_end - 1))

print()
print('=== 2. ANTIC obmedzenia ======================================')
check('stranka sa zmesti do 4K bloku (ANTIC neprenasa nad 12 bitov)',
      BPR * ROWS <= 4096, '%d B <= 4096' % (BPR * ROWS))
check('vsetky 4 stranky su na 4K hranici',
      all((PAGE0 + i * 0x1000) % 0x1000 == 0 for i in range(4)))
dl_len = 2 + 3 + (ROWS - 1) + 3
check('display list neprekroci 1K hranicu',
      (DLIST & 0xFC00) == ((DLIST + dl_len - 1) & 0xFC00),
      '$%04X..$%04X, %d B' % (DLIST, DLIST + dl_len - 1, dl_len))
check('pocet skenliniek sa zmesti do PAL ramca', 16 + ROWS * 2 <= 240,
      '16 prazdnych + %d x 2 = %d' % (ROWS, 16 + ROWS * 2))

print()
print('=== 3. tabulky ===============================================')
lut = open(os.path.join(HERE, 'out', 'gr7_pallut16.bin'), 'rb').read()
reg = open(os.path.join(HERE, 'out', 'gr7_palreg.bin'), 'rb').read()
trn = open(os.path.join(HERE, 'out', 'gr7_paltrans.bin'), 'rb').read()
check('gr7_pallut16.bin ma 32 x 16 B', len(lut) == 512, '%d B' % len(lut))
check('gr7_palreg.bin ma 32 x 4 B', len(reg) == 128, '%d B' % len(reg))
check('gr7_paltrans.bin ma 32 x 1 B (4 kody po 2 bitoch)', len(trn) == 32,
      '%d B' % len(trn))
check('vsetky hodnoty LUT su 0..3', all(v <= 3 for v in lut),
      'max %d' % max(lut))
check('LUT+PALREG sa zmestia do svojich blokov',
      PALLUT + len(lut) <= 0x4000 and PALREG + len(reg) <= DLIST + 0x100)

# PORTB bank codes vs the AUTHORITY: alt-src/Altirra/source/mmu.cpp, 320K Rambo:
#   cpuEnabled = ~portb & 0x10 ; bank = ((~pb&0x0C)>>2) | ((~pb&0x60)>>3), masked 0x0F
def altirra_320k(pb):
    if pb & 0x10:
        return None                       # window off
    return (((~pb & 0x0C) >> 2) | ((~pb & 0x60) >> 3)) & 0x0F
mine = [0x83 | ((3 - (n & 3)) << 2) | ((3 - ((n >> 2) & 3)) << 5) for n in range(16)]
check('16 kodov banky PORTB dekoduje Altirra 320K na banky 0..15',
      [altirra_320k(v) for v in mine] == list(range(16)),
      ' '.join('$%02X' % v for v in mine))
check('vsetky kody banky maju bit4=0 (okno zapnute) a bit0=1 (OS ROM)',
      all((v & 0x10) == 0 and (v & 0x01) for v in mine))
check('kody banky su navzajom rozne', len(set(mine)) == 16)

print()
print('=== 4. data v bankach ========================================')
play = open(os.path.join(HERE, 'out', 'intro_playlist_gr7.bin'), 'rb').read()
poly = open(os.path.join(PROJ, 'out', 'intro_poly.bin'), 'rb').read()
sfx = open(os.path.join(HERE, 'out', 'gr7_sfx.bin'), 'rb').read()
banks, cur = {}, None
for a, e, blob in segs:
    if a == 0x0600 and len(blob) == 6 and blob[0] == 0xA9:
        pb = blob[1]
        cur = 16 if pb & 0x10 else ((~pb & 0x0C) >> 2) | ((~pb & 0x60) >> 3)
    elif 0x4000 <= a < 0x8000 and cur is not None:
        img = banks.setdefault(cur, bytearray())
        off = a - 0x4000
        if len(img) < off:
            img += bytes(off - len(img))
        img[off:off + len(blob)] = blob
ok = True
for k in range(4):
    g = bytes(banks.get(k, b'')); ok &= g == poly[k*0x4000:k*0x4000+len(g)]
for k in range(4, 11):
    g = bytes(banks.get(k, b'')); o = (k-4)*0x4000
    ok &= g == play[o:o+len(g)]
check('obsah 11 bank sedi so zdrojovymi subormi', ok)
check('poly data su cele v bankach', sum(len(banks.get(k, b'')) for k in range(4)) == len(poly))
check('playlist je cely v bankach',
      sum(len(banks.get(k, b'')) for k in range(4, 11)) == len(play))
head = 0x8000 - len(sfx)                          # SFX start inside bank 15
b15 = bytes(banks.get(15, b'')); b16 = bytes(banks.get(16, b''))
sok = b15[head:] == sfx[:0x4000 - head] and b16 == sfx[0x4000 - head:]
sok &= not any(b15[:head])                        # nothing under the blob
check('SFX blob (koniec banky 15 + hlavna RAM pod oknom) sedi s binom', sok)
check('banky 11-14 su volne (hudba je vonku)',
      not any(banks.get(k) for k in range(11, 15)))
tabinc = open(os.path.join(HERE, 'gr7_sndv_tables.inc'), encoding='utf-8').read()
gl = int(re.search(r'^GR7_MUSIC_LEN = (\d+)', tabinc, re.M).group(1))
gs = int(re.search(r'^GR7_SFX_LEN = (\d+)', tabinc, re.M).group(1))
check('SFX-only tabulky: GR7_MUSIC_LEN=0 a GR7_SFX_LEN sedi s binom',
      gl == 0 and gs == len(sfx))
def snd_arr(name):
    row = re.search(r'^%s\s+dta\s+(.+)$' % name, tabinc, re.M).group(1)
    return [int(v.strip().lstrip('$'), 16) if v.strip().startswith('$')
            else int(v) for v in row.split(',')]
sbank, sbidx = snd_arr('sfx_bank'), snd_arr('sfx_bidx')
swlo, swhi = snd_arr('sfx_winlo'), snd_arr('sfx_winhi')
sllo, slhi = snd_arr('sfx_lenlo'), snd_arr('sfx_lenhi')
blist = snd_arr('sfx_blist')
pos, vok = 0, True
for i in range(len(sbank)):
    win = (swhi[i] << 8) | swlo[i]
    ln = (slhi[i] << 8) | sllo[i]
    off = (win - 0x4000 - head) if sbidx[i] == 0 else \
          (0x4000 - head) + (win - 0x4000)
    vok &= off == pos and ln > 0 and sbank[i] == blist[sbidx[i]]
    vok &= 0x4000 <= win < 0x8000
    pos = off + ln
check('SFX tabulka: varianty na seba naviazuju a koncia na konci blobu',
      vok and pos == len(sfx), 'koniec %d z %d' % (pos, len(sfx)))

print()
print('=== 5. assembler: lokalne navestia a vetvenia ================')
src_files = ['gr7_main.asm', 'gr7_video.asm', 'gr7_sound.asm']
dup = []
for f in src_files:
    seen, scope = {}, '(global)'
    for ln in open(os.path.join(HERE, f), encoding='utf-8'):
        m = re.match(r'^\s*\.proc\s+(\w+)', ln)
        if m: scope = m.group(1); seen.setdefault(scope, set()); continue
        if re.match(r'^\s*\.endp', ln): scope = '(global)'; continue
        m = re.match(r'^(\?\w+)', ln)
        if m:
            s = seen.setdefault(scope, set())
            if m.group(1) in s: dup.append(f'{f}:{scope}:{m.group(1)}')
            s.add(m.group(1))
check('ziadne duplicitne ? navestie v jednom rozsahu', not dup, str(dup))

BR = {0x10, 0x30, 0x50, 0x70, 0x90, 0xB0, 0xD0, 0xF0}
addrs, bad = {}, []
for ln in open(LST, encoding='latin-1'):
    m = re.match(r'^\s*\d+\s+([0-9A-F]{4})\s+([0-9A-F]{2}(?: [0-9A-F]{2})*)(.*)$', ln)
    if not m:
        continue
    if re.search(r'\bdta\b|\bins\b', m.group(3)):
        continue                               # data line, not an instruction
    addrs[int(m.group(1), 16)] = bytes.fromhex(m.group(2).replace(' ', ''))
valid = set(addrs)
for a, by in addrs.items():
    if len(by) >= 2 and by[0] in BR:
        off = by[1] - 256 if by[1] > 127 else by[1]
        t = a + 2 + off
        if t not in valid: bad.append('$%04X -> $%04X' % (a, t))
check('vsetky vetvenia trafia zaciatok instrukcie', not bad,
      '%d vetveni skontrolovanych' % sum(1 for b in addrs.values() if b and b[0] in BR))

print()
print('=== 6. mod D: masky proti alt-src/antic.cpp ==================')
# kExpand160: pixel 00->BAK(0), 01->PF0, 10->PF1, 11->PF2 ; horny nibble je vlavo
exp = None
for ln in open(os.path.join(PROJ, 'alt-src', 'Altirra', 'source', 'antic.cpp'), encoding='latin-1'):
    if '0x00, 0x01, 0x02, 0x04,' in ln: exp = True
check('kExpand160 potvrdzuje 00->BAK, 01->PF0, 10->PF1, 11->PF2', bool(exp))
vid = open(os.path.join(HERE, 'gr7_video.asm'), encoding='utf-8').read()
check('lmask = $FF,$3F,$0F,$03 (pixel 0 = bity 7-6)', 'dta $FF,$3F,$0F,$03' in vid)
check('rmask = $C0,$F0,$FC,$FF', 'dta $C0,$F0,$FC,$FF' in vid)
check('colbyte = $00,$55,$AA,$FF', 'dta $00,$55,$AA,$FF' in vid)

print()
print('=== 7. zdrojovy strom hlavnej hry ============================')
import subprocess
main_touched = []
for f in ('src/aw_polygon.asm', 'src/aw_raster.asm', 'src/aw_vbxe.asm',
          'src/aw_replayer.asm', 'src/aw_data.asm'):
    t = open(os.path.join(PROJ, f), encoding='utf-8').read()
    if 'gr7' in t.lower(): main_touched.append(f)
check('do src/ nepribudlo nic z GR.7', not main_touched, str(main_touched))
check('VBXE verzia stale pouziva MEMAC-B',
      'VBXE_MEMAC_B' in open(os.path.join(PROJ, 'src/aw_polygon.asm'), encoding='utf-8').read())

print()
print('=' * 62)
if fails:
    print('NEPRESLO: %d' % len(fails))
    for f in fails: print('   -', f)
    sys.exit(1)
print('vsetko preslo')
