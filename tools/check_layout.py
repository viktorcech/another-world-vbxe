#!/usr/bin/env python3
"""
check_layout.py - build guard for the GAME build: assert NOTHING overflows.

The game packs a lot into a tight machine -- 6502 code under the MEMAC-B window,
several RAM data blocks, and resources streamed into fixed VRAM banks. A silent
overflow (code growing past $4000, a part's video1 spilling into the bytecode
banks, two RAM blocks colliding) shows up only as a mystery crash on hardware.
This script makes those limits explicit and FAILS the build if any is exceeded.

It checks:
  1. code segment ($2000..) ends below $4000  (the MEMAC-B CPU window starts there;
     code above it is shadowed by VRAM at runtime -> crash).
  2. each streamed resource fits its VRAM bank span and does not run into the next
     region (video1 <= 4 banks before bytecode; bytecode <= 4 banks before video2; ...).
  3. the RAM data/work/VM-state blocks do not overlap each other or the code.

Usage:   python tools/check_layout.py          # builds + checks, exit 1 on any violation
"""
import os, re, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
OUT = os.path.join(PROJ, "out")
MADS = os.path.join(PROJ, "mads.exe")
ASM = os.path.join(PROJ, "src_game", "awgame.asm")
LST = os.path.join(PROJ, "awgame.lst")

CODE_ORG = 0x2000
CODE_LIMIT = 0x4000            # MEMAC-B CPU window (DATAW) -- code must stay below
BANK = 0x4000                  # 16 KB MEMAC-B bank

fails = []
warns = []


def fail(msg): fails.append(msg)
def warn(msg): warns.append(msg)


# --- 1. build with a listing, find the top of the $2000 code segment ----------
def code_top():
    r = subprocess.run([MADS, ASM, f"-o:{os.path.join(PROJ,'awgame.xex')}", f"-l:{LST}"],
                       cwd=PROJ, capture_output=True, text=True)
    if r.returncode != 0:
        fail("mads build FAILED:\n" + (r.stdout or "") + (r.stderr or ""))
        return None
    hi = 0
    for ln in open(LST, encoding="latin-1"):
        m = re.match(r"\s*\d+\s+([0-9A-Fa-f]{4})\s", ln)
        if m:
            a = int(m.group(1), 16)
            if CODE_ORG <= a < CODE_LIMIT:
                hi = max(hi, a)
    return hi


# --- 2. VRAM resource spans (bank base -> max banks before the next region) ----
# layout: video1 $14 (4 banks) | bytecode $18 (4 banks) | video2 $1C (4 banks)
VRAM = [
    ("video1  (water_v1.bin)", "water_v1.bin", 0x14, 0x18),   # must stay below bank $18
    ("bytecode(water_code.bin)", "water_code.bin", 0x18, 0x1C),
    ("video2  (water_v2.bin)", "water_v2.bin", 0x1C, 0x20),
]


def check_vram():
    for label, fn, base, nextbase in VRAM:
        p = os.path.join(OUT, fn)
        if not os.path.exists(p):
            warn(f"{label}: {fn} missing (run tools/game_pack.py)")
            continue
        sz = os.path.getsize(p)
        span = (nextbase - base) * BANK            # bytes available before the next region
        end_bank = base + (sz - 1) // BANK
        if sz > span:
            fail(f"{label}: {sz} B > {span} B available (banks ${base:02X}..${nextbase-1:02X}) "
                 f"-> overruns into bank ${nextbase:02X}")
        else:
            print(f"  ok  {label:26} {sz:6} B  banks ${base:02X}..${end_bank:02X}  "
                  f"({span-sz} B headroom)")


# --- 3. RAM blocks must not overlap (static map mirrors aw_equates + game_vm) ---
def check_ram(code_end):
    blocks = [
        ("code",        CODE_ORG, code_end),
        ("pal_data",    0x9000, 0x9000 + 1536),
        ("SNAP",        0x9600, 0x9600 + 328 + 512),    # thread snapshot + globals + vars (game_vm)
        ("RAMB work",   0x9C00, 0x9C00 + 384 + 256),    # locals..pstk(+384)+256
        ("cc arena tbl",0x9E80, 0x9EA7),                # game_cellcache 5-arena alloc table
        ("fmul tables", 0xA000, 0xA800),
        ("poly LUTs",   0xA800, 0xAA00),
        ("cell cache",  0xAA00, 0xB000),                # game_cellcache.asm (ert-guarded)
        ("VM state",    0xB000, 0xB3FF),                # var_lo .. cc_* work vars (incl. cc_roff)
    ]
    blocks.sort(key=lambda b: b[1])
    for a, b in zip(blocks, blocks[1:]):
        if a[2] > b[1]:
            fail(f"RAM overlap: {a[0]} (${a[1]:04X}..${a[2]:04X}) into "
                 f"{b[0]} (${b[1]:04X}..${b[2]:04X})")
    for name, lo, hi in blocks:
        if hi > 0xC000:
            fail(f"{name} (${lo:04X}..${hi:04X}) runs into hardware/ROM at $C000")
    print("  RAM blocks:", ", ".join(f"{n} ${lo:04X}-${hi:04X}" for n, lo, hi in blocks))


# --- 4. access-code page-snapshot holes (game_vm.asm pages_xfer / PSAV0-3) -----
# Entering 16008 by 'C' saves the 4 LR pages to $052000/$062000/$070000/$078000;
# they must survive load_part(16008), so 16008's video1 must stay below $052000
# (<= 8192 B), its bytecode below $062000 (<= 8192 B), and it must have NO video2
# and NO sound banks. Parsed from the generated src_game/game_atr.inc (128 B sectors).
def check_snapshot_holes():
    inc = os.path.join(PROJ, "src_game", "game_atr.inc")
    if not os.path.exists(inc):
        warn("game_atr.inc missing -- 16008 snapshot-hole check skipped")
        return
    txt = open(inc).read()
    def counts(label):
        m = re.search(label + r"\s*\n\s*dta ([0-9,]+)", txt)
        return [int(v) for v in m.group(1).split(",")] if m else None
    pairs = {}
    for name in ("atr_v1_cnt", "atr_code_cnt", "atr_v2_cnt", "atr_snd_cnt"):
        lo = counts(name + "_lo"); hi = counts(name + "_hi")
        if lo is None:                      # 8-bit table (e.g. atr_v2_cnt has no _hi? keep safe)
            lo = counts(name); hi = [0] * len(lo) if lo else None
        if lo is None:
            warn(f"{name}: not found in game_atr.inc -- snapshot-hole check incomplete")
            continue
        pairs[name] = (lo[-1] | (hi[-1] << 8)) * 128   # part idx 8 = 16008, bytes
    lim = {"atr_v1_cnt": 8192, "atr_code_cnt": 8192, "atr_v2_cnt": 0, "atr_snd_cnt": 0}
    for name, sz in pairs.items():
        if sz > lim[name]:
            fail(f"16008 {name[4:-4]} = {sz} B > {lim[name]} B -- it would clobber the "
                 f"access-code page-snapshot slots (game_vm.asm PSAV0-3)")
        else:
            print(f"  ok  16008 {name[4:-4]:5} {sz:5} B (snapshot-hole limit {lim[name]})")


def main():
    print("== game build layout guard ==")
    top = code_top()
    if top is not None:
        end = top + 1
        head = CODE_LIMIT - end
        tag = "ok " if head > 0 else "FAIL"
        print(f"  {tag} code  $2000..${top:04X}   headroom to $4000 = {head} B")
        if head <= 0:
            fail(f"code reaches ${top:04X} >= $4000 (overlaps the MEMAC-B window)")
        check_ram(end)
    print("VRAM resources:")
    check_vram()
    print("16008 page-snapshot holes:")
    check_snapshot_holes()

    print()
    for w in warns:
        print("WARN:", w)
    if fails:
        for f in fails:
            print("FAIL:", f)
        print(f"\n{len(fails)} violation(s) -- build is NOT safe.")
        sys.exit(1)
    print("All layout checks passed.")


if __name__ == "__main__":
    main()
