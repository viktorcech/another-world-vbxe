#!/usr/bin/env python3
"""pack_xex.py - a BUILD STEP of build.ps1: ZX02-pack a plain xex for the disk.
The plain xex stays in the root for every tool that reads it; the packed file is
what buildstep_join_disk.py puts on the sectors. Used for both programs:
    awintro.xex -> out/awintro_zx.bin      (sectors 4..)
    awgame.xex  -> out/awgame_zx.bin       (GAME_SEC.., reached by the intro chain)

  packed = src_game/xex_unpack.asm as a plain XEX (segment + INI, loaded and
  started by the boot loader) + a record stream only that code reads:
      0, lo, hi, <ZX02 stream>    data segment, unpacked to its own address
      1, lo, hi                   INIT (a $02E2 segment)
      2, lo, hi                   RUN  (the $02E0 segment, must be last)
  in the plain xex's segment order.

The unpacker calls the boot loader's read_sec and reads its buffer, so the loader's
addresses are taken from its label table (out/boot.lab, `mads -t`).

  python tools/pack_xex.py --in awgame.xex --out out/awgame_zx.bin [--pad-to N]
      --pad-to N : pad the file to exactly N sectors (fail if it needs more). The
                   build packs twice -- the second pass bakes in sector numbers
                   derived from the first -- and the size must not move between.
Prints the packed size in sectors on its last line.
"""
import os, sys, struct, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
OUT = os.path.join(PROJ, "out")
sys.path.insert(0, HERE)
import make_game_atr as M                                         # noqa: E402

SECTOR = 128
STAGE2_SRC = os.path.join(PROJ, "src_game", "xex_unpack.asm")
STAGE2 = os.path.join(OUT, "xex_unpack.xex")
BOOT_LAB = os.path.join(OUT, "boot.lab")
MADS = os.path.join(PROJ, "mads.exe")


def segments(d):
    """plain XEX -> [(start, bytes)] in file order"""
    out, i = [], 0
    while i < len(d):
        if d[i:i + 2] == b"\xff\xff":
            i += 2
            continue
        s, e = struct.unpack("<HH", d[i:i + 4])
        i += 4
        out.append((s, d[i:i + e - s + 1]))
        i += e - s + 1
    return out


def boot_labels():
    lab = {}
    for ln in open(BOOT_LAB, encoding="latin-1"):
        p = ln.split()
        if len(p) == 3:
            lab[p[2].upper()] = int(p[1], 16)
    return lab


def stage2():
    """assemble xex_unpack.asm against the current boot loader -> (xex bytes, lo, hi)"""
    lab = boot_labels()
    defs = [f"-d:BOOT_{n}={lab[k]}" for n, k in
            (("BUFPOS", "BUF_POS"), ("SECBUF", "SECBUF"), ("READSEC", "READ_SEC"))]
    r = subprocess.run([MADS, STAGE2_SRC, *defs, f"-o:{STAGE2}"], capture_output=True,
                       text=True)
    if r.returncode or not os.path.exists(STAGE2):
        sys.exit(f"stage 2 assembly failed:\n{r.stdout}\n{r.stderr}")
    img = open(STAGE2, "rb").read()
    lo = img[2] | (img[3] << 8)
    return img, lo, img[4] | (img[5] << 8)


def main():
    arg = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    src, dst = arg("--in"), arg("--out")
    if not src or not dst:
        sys.exit(__doc__)
    pad_to = arg("--pad-to")
    img2, s2lo, s2hi = stage2()

    segs = segments(open(src, "rb").read())
    name = os.path.basename(src)
    if segs[-1][0] != 0x2E0 or any(s == 0x2E0 for s, _ in segs[:-1]):
        sys.exit(f"{name} must end in its (only) RUN segment")
    for s, b in segs:
        if s not in (0x2E0, 0x2E2) and s <= s2hi and s + len(b) > s2lo:
            sys.exit(f"{name}: segment ${s:04X}-${s + len(b) - 1:04X} overlaps the "
                     f"unpacker at ${s2lo:04X}-${s2hi:04X}")
    data = [b for s, b in segs if s not in (0x2E0, 0x2E2)]
    if any(len(b) > 0x4000 for b in data):
        sys.exit(f"{name}: a data segment is over 16 KB (the unpacker's ZX02 window)")
    packed = M.zx02_pack_all(data)

    rec, k = bytearray(), 0
    for s, b in segs:
        if s in (0x2E0, 0x2E2):
            if len(b) != 2:
                sys.exit(f"{name}: ${s:04X} segment of {len(b)} bytes")
            rec += bytes((2 if s == 0x2E0 else 1,)) + b
        else:
            rec += bytes((0, s & 0xFF, s >> 8)) + packed[k]
            k += 1

    # read the records back the way xex_unpack does and require the plain segments
    back, p = [], 0
    while True:
        tag, a = rec[p], rec[p + 1] | (rec[p + 2] << 8)
        p += 3
        if tag == 0:
            got, p = M.zx02_decode(rec, p)
            back.append((a, got))
        else:
            back.append((0x2E2 if tag == 1 else 0x2E0, bytes((a & 0xFF, a >> 8))))
            if tag == 2:
                break
    if back != [(s, bytes(b)) for s, b in segs] or p != len(rec):
        sys.exit(f"PACK_XEX VERIFY FAILED: the records do not unpack to {name}")

    img = img2 + rec
    n = M.secs(len(img))
    if pad_to is not None:
        if n > int(pad_to):
            sys.exit(f"packed {name} needs {n} sectors, the layout was fixed at {pad_to}")
        n = int(pad_to)
    open(dst, "wb").write(img.ljust(n * SECTOR, b"\x00"))
    plain = sum(len(b) for _, b in segs)
    print(f"[zx02] {name}: {plain // 1024} KB in {len(segs)} segments -> "
          f"{len(img) // 1024} KB ({len(img) / plain:.2f}), stage 2 {len(img2)} B")
    print(n)


if __name__ == "__main__":
    main()
