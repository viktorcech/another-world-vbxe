#!/usr/bin/env python3
"""
make_game_atr.py - build the bootable GAME disk (awgame.atr), the Another World
counterpart to doom2d/tools/make_atr.py.

The full game is ~1.7 MB raw -- too big for VRAM/RAM -- so each part loads from disk
on demand (one part resident at a time, overwriting fixed VRAM banks). This lays a
3-sector boot loader + awgame.xex + every part's RAW resources onto an ATR, and emits
the asm sector table (src_game/game_atr.inc) the 6502 load_part reads.

  awgame.atr               bootable, game-only (the intro is the separate awvbxe build)
  src_game/game_atr.inc    per-part {sector, count} tables (auto-generated)

SPEED: the part data is fixed AW content, so the slow depacking is done ONCE and the
sector-aligned blob is cached (out/game_parts.bin + .json). Later builds (and the
second pass) just splice boot + the new xex + the cached blob -- no re-depacking.
Use --force to rebuild the cache (e.g. after changing PARTS).

Run from the project root:   python tools/make_game_atr.py [--force]
"""
import os, sys, struct, json
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
OUT = os.path.join(PROJ, "out")
INC = os.path.join(PROJ, "src_game", "game_atr.inc")
sys.path.insert(0, HERE)
import aw_pack
from game_pack import MEMLIST_PARTS, pal_bytes

SECTOR = 128
LR_BMP = 160 * 200                              # a decoded background bitmap = one LR page

# --- SFX support: each part's sounds are loaded with the part into VRAM banks
# $11-$13 ($044000, the free 48 KB between the control bank $10 and the poly banks
# $14). Stored as 4-bit POKEY nibbles at NATIVE length; the player sets AUDF1 from
# op_sound's freq byte (the AW period table) so the pitch is correct.
# A part's full sound set is loaded with the part into 7 free, NON-contiguous VRAM
# banks (the gaps the video pages / control / poly / video2 leave). The player walks
# them via this bank list; the 6502 directory addresses each sound by (bank-list
# index, window, length). Max part (jail) ~90 KB fits in these 7 banks (112 KB).
SND_BLIST = [0x0E, 0x0F, 0x11, 0x12, 0x13, 0x1E, 0x1F]
PERIOD_TABLE = [1076,1016,960,906,856,808,762,720,678,640,604,570,538,508,480,453,
                428,404,381,360,339,320,302,285,269,254,240,226,214,202,190,180,170,
                160,151,143,135,127,120,113]

# AUDF1 per AW freq byte (POKEY 64 kHz base: rate = 63921/(AUDF1+1); AW target
# rate = kPaulaFreq/(period*2)). Shared by the .inc emission and the rate cap.
AUDF_TAB = [max(1, round(63921 * p * 2 / 7159092) - 1) for p in PERIOD_TABLE]

# Sample-rate cap (2026-06-10): the 6502 plays one nibble per Timer-1 IRQ, so the
# IRQ fires at the sample rate. Rates above ~10.6 kHz cost 70-126% of the CPU --
# the >=16 kHz classes SATURATE it (handler ~100 cyc > 83-cyc period), playing
# ~1/3 flat AND freezing the game. Any (resId,freq) combo whose native AUDF is
# below AUDF_CAP ships an extra, BUILD-TIME RESAMPLED variant (anti-aliased, from
# the full 8-bit PC source in pc/) that plays at AUDF_CAP with the CORRECT pitch.
AUDF_CAP = 5                      # floor rate = 63921/6 ~= 10653 Hz


def _be16(b, o):
    return (b[o] << 8) | b[o + 1]


def snd_addr(off):
    """blob byte offset -> (bank-list index, window lo, window hi). The blob is laid
    out across SND_BLIST banks in order (16 KB each)."""
    win = 0x4000 | (off & 0x3FFF)
    return off >> 14, win & 0xFF, win >> 8


def sound_pcm(me):
    """AW sound resource (8-byte header + 8-bit SIGNED PCM) -> the raw signed body."""
    data, _ = aw_pack.load_resource(me)
    if len(data) < 8:
        return b""
    ln = _be16(data, 0); loop = _be16(data, 2)
    return data[8:8 + (ln + loop) * 2]


def pack_nibbles(body):
    """8-bit SIGNED PCM -> 4-bit POKEY nibbles (2/byte, hi first), native length.
    signed -> unsigned amplitude = byte ^ 0x80."""
    nibs = [(b ^ 0x80) >> 4 for b in body]
    if len(nibs) & 1:
        nibs.append(8)
    return bytes((nibs[i] << 4) | nibs[i + 1] for i in range(0, len(nibs), 2))


def sound_to_4bit(me):
    return pack_nibbles(sound_pcm(me))


def resample_pcm(body, factor):
    """Decimate signed 8-bit PCM to round(len*factor) samples (factor < 1): box
    anti-alias filter (mean over the source window). PC-side from the full 8-bit
    source, so the capped variant keeps the correct pitch and all content below
    the new Nyquist. Quality-first; speed irrelevant (cached build step)."""
    if not body or factor >= 1.0:
        return body
    sig = [b - 256 if b >= 128 else b for b in body]
    n = max(1, int(round(len(sig) * factor)))
    w = len(sig) / n                          # source samples per output sample
    out = bytearray()
    for i in range(n):
        j0 = int(i * w)
        j1 = min(max(j0 + 1, int(round((i + 1) * w))), len(sig))
        v = int(round(sum(sig[j0:j1]) / (j1 - j0)))
        out.append(v & 0xFF)
    return bytes(out)


def collect_sound_freqs():
    """Every (resId, freq) combo any part's bytecode can request via op_sound
    (opcode 0x18: res16, freq8, vol8, ch8). STATIC linear scan -- the freq operand
    is always a LITERAL byte in AW bytecode (rawgl fetchByte), and scanning every
    byte offset yields a SUPERSET of the real combos (false positives only waste
    a few bytes of VRAM; false negatives cannot happen)."""
    mem = aw_pack.read_memlist()
    bag = {}
    for part in PARTS:
        code = aw_pack.load_resource(mem[MEMLIST_PARTS[part][1]])[0]
        i, n = 0, len(code) - 4
        s = bag.setdefault(part, set())
        while i < n:
            if code[i] == 0x18:                       # op_sound <res16> <freq8> ...
                num = (code[i + 1] << 8) | code[i + 2]
                s.add((num, code[i + 3]))
            i += 1
    return bag


def collect_sounds():
    """Per part, its FULL sound set -- the type-0 resources it op_MEMLISTs at part
    start (AW loads the whole set up front, frame ~3), NOT just the op_sounds that a
    no-input sim run happens to reach. This is the complete, input-independent list."""
    import game_sim
    mem = aw_pack.read_memlist()
    OPS = game_sim.GameVM.OPS
    i_m = next(i for i, f in enumerate(OPS) if f.__name__ == 'op_memlist')
    orig = OPS[i_m]
    bag = {}
    cur = [None]

    def ml(self):
        pc = self.pc
        num = (self.code[pc] << 8) | self.code[pc + 1]
        if num < len(mem) and getattr(mem[num], 'type', 0) == 0 \
                and getattr(mem[num], 'size', 0) > 0:
            bag.setdefault(cur[0], set()).add(num)
        return orig(self)

    OPS[i_m] = ml
    try:
        for part in PARTS:
            cur[0] = part
            try:
                game_sim.GameVM(part, 'int').run(150)   # the memlist batch is at frame ~3
            except Exception:
                pass
    finally:
        OPS[i_m] = orig
    return {p: sorted(s) for p, s in bag.items()}
BOOT = os.path.join(OUT, "boot.bin")            # 3-sector XEX boot loader
XEX = os.path.join(PROJ, "awgame.xex")          # the game program (loaded by boot)
ATR = os.path.join(PROJ, "awgame.atr")          # bootable, game-only, mount on D1:
CACHE_BLOB = os.path.join(OUT, "game_parts.bin")    # depacked, sector-aligned part data
CACHE_META = os.path.join(OUT, "game_parts.json")   # {parts, table} for the blob

# parts to put on the disk, in this index order (the 6502 maps part# -> index)
PARTS = [16000, 16001, 16002, 16003, 16004, 16005, 16006, 16007, 16008]


def secs(n):                        # bytes -> whole 128-byte sectors
    return (n + SECTOR - 1) // SECTOR


# --- background BITMAP support (luxe etc.: op_memlist loads a 32 KB type-2 bitmap) ----
def decode_bitmap_lr(me):
    """Decode a 32000-byte AW background bitmap (4 planes x 8000 B, LSB-first bit order)
    into a 32000-byte LR (160x200) palette-INDEX page -- the exact format a VBXE LR page
    holds, so the 6502 just streams it to VRAM page 0 (no on-Atari decode; the runtime
    palette colours it). Keeps even 320-columns, like the LR polygon path."""
    data, _ok = aw_pack.load_resource(me)
    out = bytearray(LR_BMP)
    for y in range(200):
        base = y * 160
        for xb in range(40):
            o = y * 40 + xb
            b0, b1, b2, b3 = data[o], data[o + 8000], data[o + 16000], data[o + 24000]
            for bit in range(8):
                m = 1 << bit                    # LSB-first
                x = xb * 8 + (7 - bit)
                if x & 1:
                    continue                    # LR keeps even columns
                out[base + (x >> 1)] = (1 if b0 & m else 0) | (2 if b1 & m else 0) \
                    | (4 if b2 & m else 0) | (8 if b3 & m else 0)
    return bytes(out)


def collect_bitmaps():
    """Bitmap resource numbers any shipped part loads via op_memlist -- found by a STATIC
    scan of each part's bytecode (opcode 0x19 = op_memlist, followed by a big-endian 16-bit
    resource number that resolves to a 32 KB type-2 bitmap).

    The OLD approach ran the VM with NO input for a few frames, so it only saw bitmaps loaded
    in the FIRST room of each part (luxe 144/145) and MISSED bitmaps loaded in later rooms you
    only reach by PLAYING -- jail's 72/73 (the rocky cells/elevator), cite's 67-70, water's 19.
    Those backgrounds were never decoded onto the ATR, so op_memlist couldn't find them and the
    scenery rendered as bare polygons (the "missing jail textures" bug -- same class as luxe's
    old black background). The static scan finds every referenced bitmap regardless of input."""
    mem = aw_pack.read_memlist()
    bmps = {i for i, m in enumerate(mem)
            if getattr(m, 'type', 0) == 2 and getattr(m, 'size', 0) == 32000}
    bag = set()
    for part in PARTS:
        code = aw_pack.load_resource(mem[MEMLIST_PARTS[part][1]])[0]
        i, n = 0, len(code) - 2
        while i < n:
            if code[i] == 0x19:                       # op_memlist <res16>
                num = (code[i + 1] << 8) | code[i + 2]
                if num in bmps:
                    bag.add(num)
            i += 1
    return sorted(bag)


# --- depack all parts into one sector-aligned blob (SLOW; cached) --------------------
def build_blob():
    mem = aw_pack.read_memlist()
    blob = bytearray()
    table = {}                      # part -> ((rel_sector_0based, count) x [pal,code,v1,v2])

    def place(b):
        if not b:
            return (0, 0)
        start = len(blob) // SECTOR
        blob.extend(b)
        blob.extend(b"\x00" * ((-len(blob)) % SECTOR))
        return (start, secs(len(b)))

    for part in PARTS:
        pa, co, v1, v2 = MEMLIST_PARTS[part]
        table[part] = (
            place(pal_bytes(aw_pack.load_resource(mem[pa])[0])),
            place(aw_pack.load_resource(mem[co])[0]),
            place(aw_pack.load_resource(mem[v1])[0]),
            place(aw_pack.load_resource(mem[v2])[0]) if v2 else (0, 0),
        )
    bmp = {}                        # resource num -> (rel_sector, count) of the decoded LR page
    for num in collect_bitmaps():
        bmp[num] = place(decode_bitmap_lr(mem[num]))

    # per-part SFX : a 4-bit blob placed on the ATR, plus a directory the 6502
    # op_sound searches. Entry = (resId, freq, audf, blidx, winlo, winhi, lnlo,
    # lnhi): freq $FF = native wildcard (any freq; AUDF from the snd_audf table),
    # else an exact (resId,freq) match pointing at a BUILD-TIME RESAMPLED variant
    # played at audf (= AUDF_CAP). Capped variants come FIRST in a part's slice
    # so the 6502's first-match scan prefers them over the wildcard.
    snd_sets = collect_sounds()
    snd_freqs = collect_sound_freqs()
    snd_table = {}                  # part -> (rel_sector, count) of its sound blob
    snd_dir = []                    # flat entry list (see above)
    snd_pdir = {}                   # part -> (dir_start_index, entry_count)
    ncap = nwild_dropped = 0
    for part in PARTS:
        sounds = snd_sets.get(part, [])
        combos = snd_freqs.get(part, set())
        start = len(snd_dir)
        pblob = bytearray()

        def add_entry(num, freq, d):
            bi, wlo, whi = snd_addr(len(pblob))
            pblob.extend(d)
            ln = len(d)
            snd_dir.append((num, freq, bi, wlo, whi, ln & 0xFF, (ln >> 8) & 0xFF))

        # capped variants first (resampled so rate <= ~10.6 kHz, pitch correct;
        # all play at AUDF_CAP, so no per-entry audf is stored)
        for (num, freq) in sorted(combos):
            if num not in sounds:
                continue                  # scan false positive / other part's resource
            audfv = AUDF_TAB[min(freq, 39)]
            if audfv >= AUDF_CAP:
                continue                  # native rate is affordable -> wildcard serves it
            factor = (audfv + 1) / (AUDF_CAP + 1)
            d = pack_nibbles(resample_pcm(sound_pcm(mem[num]), factor))
            if d:
                add_entry(num, freq, d)
                ncap += 1
        # native wildcards -- only for sounds some request can still reach natively:
        # the scan is a SUPERSET of real op_sounds, so a sound whose every scanned
        # freq is capped can never be requested at a native rate -> no wildcard.
        for num in sounds:
            fs = [f for (n, f) in combos if n == num]
            if fs and all(AUDF_TAB[min(f, 39)] < AUDF_CAP for f in fs):
                nwild_dropped += 1
                continue
            d = sound_to_4bit(mem[num])
            add_entry(num, 0xFF, d)
        snd_pdir[part] = (start, len(snd_dir) - start)
        if len(pblob) > len(SND_BLIST) * 0x4000:
            sys.exit(f"ERROR: part {part} sound set {len(pblob)} B > {len(SND_BLIST)} "
                     f"banks ({len(SND_BLIST)*0x4000} B). Drop sounds or add a bank.")
        snd_table[part] = place(bytes(pblob)) if pblob else (0, 0)
    if len(snd_dir) > 256:
        sys.exit(f"ERROR: sound directory {len(snd_dir)} entries > 256 (X-indexed).")
    print(f"[sfx] {ncap} rate-capped variants (>{63921//(AUDF_CAP+1)} Hz -> resampled), "
          f"{nwild_dropped} always-capped wildcards dropped")
    return bytes(blob), table, bmp, snd_table, snd_dir, snd_pdir


def load_or_build_blob(force):
    if not force and os.path.exists(CACHE_BLOB) and os.path.exists(CACHE_META):
        meta = json.load(open(CACHE_META))
        if meta.get("parts") == PARTS and meta.get("sndv") == 2:
            blob = open(CACHE_BLOB, "rb").read()
            table = {int(k): tuple(tuple(x) for x in v) for k, v in meta["table"].items()}
            bmp = {int(k): tuple(v) for k, v in meta["bmp"].items()}
            snd_table = {int(k): tuple(v) for k, v in meta["snd_table"].items()}
            snd_dir = [tuple(e) for e in meta["snd_dir"]]
            snd_pdir = {int(k): tuple(v) for k, v in meta["snd_pdir"].items()}
            print(f"[cache] reusing depacked part blob ({len(blob)//1024} KB)")
            return blob, table, bmp, snd_table, snd_dir, snd_pdir
    print("[cache] depacking all parts + bitmaps + sounds (first build / parts changed)...")
    blob, table, bmp, snd_table, snd_dir, snd_pdir = build_blob()
    os.makedirs(OUT, exist_ok=True)
    open(CACHE_BLOB, "wb").write(blob)
    json.dump({"parts": PARTS, "sndv": 2,
               "table": {str(k): v for k, v in table.items()},
               "bmp": {str(k): v for k, v in bmp.items()},
               "snd_table": {str(k): v for k, v in snd_table.items()},
               "snd_dir": snd_dir,
               "snd_pdir": {str(k): v for k, v in snd_pdir.items()}}, open(CACHE_META, "w"))
    return blob, table, bmp, snd_table, snd_dir, snd_pdir


# --- ZX02 packing of the part blob (2026-09-27) ----------------------------------
# The disk carries every resource ZX02-packed (DMSC's 6502 format, ~0.52 of raw on
# this data vs ~0.54 DEFLATE, and a 140-byte table-free decoder). The 6502
# (src_game/game_unpack.asm) decodes straight into the MEMAC-B window, so each
# resource is cut into 16 KB bank chunks and every chunk is its OWN stream: a
# back-reference never has to reach into the previous bank. A resource's streams
# are concatenated and padded to a sector PAIR (pk_fill always reads two).
# Sector counts in game_atr.inc stay RAW (the cell cache sizes its arenas by them
# and they give the 6502 the number of bank chunks); only the start sectors move.
ZX02_EXE = os.path.join(PROJ, "mads-src", "compression", "zx2", "pc", "zx02.exe")
ZX02_CACHE = os.path.join(OUT, "zx02_cache")      # sha1(raw chunk) -> packed chunk
PACKED_BLOB = os.path.join(OUT, "game_parts_zx.bin")   # what goes onto the disk
BANK = 0x4000
PK_ALIGN = 2 * SECTOR


def zx02_decode(d, src=0):
    """Bit-for-bit model of the 6502 decoder in src_game/game_unpack.asm (zx02-optim
    shape, including its unchecked bit reads) -- the build fails if a stream does
    not come back byte-exact through THIS, not through the C reference.
    Decodes the stream at d[src:]; returns (output, where reading stopped)."""
    out = bytearray()
    st = {"bitr": 0x80}

    def elias(x, c=None):
        while True:
            if c is None:                        # get_elias: asl bitr / bne
                b = st["bitr"]
                c, st["bitr"] = b >> 7, (b << 1) & 0xFF
                if st["bitr"] == 0:              # sentinel shifted out: next byte
                    nonlocal src
                    v = d[src]; src += 1
                    st["bitr"] = ((v << 1) | c) & 0xFF
                    c = v >> 7
            if not c:                            # elias_skip1: 0 ends the code
                return x
            b = st["bitr"]                       # elias_get: data bit, unchecked
            st["bitr"] = (b << 1) & 0xFF
            x = ((x << 1) | (b >> 7)) & 0xFF
            c = None

    def bit():                                   # a bare asl bitr
        b = st["bitr"]
        st["bitr"] = (b << 1) & 0xFF
        return b >> 7

    def copy(dist, n):
        for _ in range(n or 256):
            out.append(out[len(out) - dist])

    off = 0                                      # offset-1, as the 6502 keeps it
    x = elias(1)
    while True:
        n = x or 256                             # decode_literal
        out += d[src:src + n]; src += n
        if not bit():
            copy(off + 1, elias(1))              # repeat the last offset
            if not bit():
                x = elias(1)
                continue
        while True:                              # dzx0s_new_offset
            x = elias(1)
            if x == 0:
                return bytes(out), src
            v = d[src]; src += 1
            off = (((x - 1) & 0xFF) << 7) | (v >> 1)
            copy(off + 1, (elias(1, v & 1) + 1) & 0xFF)
            if bit():
                continue
            x = elias(1)
            break


def zx02_encode_greedy(data):
    """Plain greedy ZX02 encoder (same bitstream as zx02.exe's compress.c). Only the
    fallback for chunks zx02.exe gets WRONG: on very long runs (a 13 KB zero run in
    a bitmap) it emits an Elias length > 256 that no decoder can read."""
    out = bytearray()
    st = {"mask": 0, "at": 0, "back": True}

    def bit(v):
        if st["back"]:                           # rides in bit 0 of the offset LSB
            st["back"] = False
            if v:
                out[-1] |= 1
            return
        if not st["mask"]:
            st["mask"], st["at"] = 0x80, len(out)
            out.append(0)
        if v:
            out[st["at"]] |= st["mask"]
        st["mask"] >>= 1

    def elias(v):                                # 1..256, 256 sent as 0x100
        i = 1
        while i * 2 <= v:
            i *= 2
        while i > 1:
            i >>= 1
            bit(1)
            bit(v & i)
        bit(0)

    n = len(data)
    heads = {}
    last, i, lit_start, prev_lit = 1, 0, 0, False

    def best_match(i):
        bl, bo = 0, 0
        cands = [1, last] + [i - p for p in heads.get(data[i:i + 2], [])[-48:][::-1]]
        for o in cands:
            if o < 1 or o > i:
                continue
            l = 0
            while l < 256 and i + l < n and data[i + l] == data[i + l - o]:
                l += 1
            if l > bl or (l == bl and o == last):
                bl, bo = l, o
        return bl, bo

    def flush_lits(i):
        nonlocal lit_start, prev_lit
        if i > lit_start:
            bit(0)
            elias(i - lit_start)
            out.extend(data[lit_start:i])
            prev_lit = True

    def add_head(j):
        if j + 1 < n:
            heads.setdefault(data[j:j + 2], []).append(j)

    while i < n:
        l, o = best_match(i)
        lits = i - lit_start
        rep_ok = o == last and (lits > 0 or prev_lit)
        need = 1 if rep_ok else (2 if o <= 128 * 4 else 3)
        if (l >= need and i > 0) or lits == 256:
            if l < 1:
                sys.exit("zx02 greedy: 256 literals with no match to break them")
            flush_lits(i)
            if o == last and prev_lit:           # repeat-offset match
                bit(0)
                elias(l)
            else:
                bit(1)
                elias((o - 1) // 128 + 1)
                out.append(((o - 1) % 128) << 1)
                st["back"] = True
                elias(l - 1 if l > 1 else 256)
            last, prev_lit = o, False
            for j in range(i, i + l):
                add_head(j)
            i += l
            lit_start = i
        else:
            add_head(i)
            i += 1
    flush_lits(i)
    bit(1)                                       # end marker = new offset, MSB 256
    elias(256)
    return bytes(out)


def zx02_pack_all(chunks):
    """ZX02-pack every distinct chunk once (cached by content, zx02.exe run in
    parallel -- ~2 s per 16 KB) and verify each through zx02_decode."""
    import hashlib, subprocess
    from concurrent.futures import ThreadPoolExecutor
    os.makedirs(ZX02_CACHE, exist_ok=True)
    keys = {hashlib.sha1(c).hexdigest(): c for c in chunks}

    def one(k):
        p = os.path.join(ZX02_CACHE, k + ".zx2")
        if not os.path.exists(p):
            raw = os.path.join(ZX02_CACHE, k + ".bin")
            open(raw, "wb").write(keys[k])
            r = subprocess.run([ZX02_EXE, "-f", raw, p], capture_output=True)
            os.remove(raw)
            if r.returncode or not os.path.exists(p):
                sys.exit(f"zx02 failed on chunk {k}: {r.stdout!r} {r.stderr!r}")
        return k, open(p, "rb").read()

    todo = sum(not os.path.exists(os.path.join(ZX02_CACHE, k + ".zx2")) for k in keys)
    if todo:
        print(f"[zx02] packing {todo} new chunk(s) of {len(keys)}...")
    with ThreadPoolExecutor(os.cpu_count() or 4) as ex:
        packed = dict(ex.map(one, keys))
    def trim(k, z):
        """-> z cut to exactly what the decoder consumes, or None if it does not
        round-trip. The cut matters: a resource's streams sit back to back and the
        6502 starts the next one right where this one stopped reading, but zx02.exe
        leaves 1-4 bytes behind its end marker."""
        try:
            got, used = zx02_decode(z)
        except IndexError:                       # ran off the end of the stream
            return None
        return z[:used] if got == keys[k] else None

    nfb = 0
    for k, z in packed.items():
        t = trim(k, z)
        if t is None:
            t = trim(k, zx02_encode_greedy(keys[k]))   # zx02.exe's long-run bug
            if t is None:
                sys.exit(f"ZX02 VERIFY FAILED on chunk {k}: neither zx02.exe nor the "
                         f"greedy fallback round-trips through the 6502 decoder model")
            nfb += 1
        packed[k] = t
    if nfb:
        print(f"[zx02] {nfb} chunk(s) re-encoded by the greedy fallback")
    return {k: packed[hashlib.sha1(c).hexdigest()] for k, c in enumerate(chunks)}


def pack_blob(blob, table, bmp, snd_table):
    """raw blob + (rel_sector, count) entries -> packed blob + the same entries with
    rel_sector moved into the packed blob (count stays raw)."""
    ents = sorted({e for t in table.values() for e in t if e[1]}
                  | {e for e in bmp.values() if e[1]}
                  | {e for e in snd_table.values() if e[1]})
    chunks, owner = [], []
    for e in ents:
        raw = blob[e[0] * SECTOR:(e[0] + e[1]) * SECTOR]
        for o in range(0, len(raw), BANK):
            chunks.append(raw[o:o + BANK]); owner.append(e)
    packed = zx02_pack_all(chunks)
    out = bytearray()
    where = {}
    by_content = {}                 # raw bytes -> location: identical resources are
    skip = set()                    #   stored ONCE, and load_part relies on it (a v2
    for i, e in enumerate(owner):   #   whose start sector matches is still resident)
        if e not in where:
            raw = blob[e[0] * SECTOR:(e[0] + e[1]) * SECTOR]
            if raw in by_content:
                where[e] = by_content[raw]
                skip.add(e)
                continue
            out.extend(b"\x00" * ((-len(out)) % PK_ALIGN))
            where[e] = by_content[raw] = (len(out) // SECTOR, e[1])
        if e not in skip:
            out.extend(packed[i])
    out.extend(b"\x00" * ((-len(out)) % PK_ALIGN))
    # the whole disk image as the 6502 reads it: every resource's streams back to back
    for e, (sec, cnt) in where.items():
        raw, pos = blob[e[0] * SECTOR:(e[0] + cnt) * SECTOR], sec * SECTOR
        for o in range(0, len(raw), BANK):
            got, pos = zx02_decode(out, pos)
            if got != raw[o:o + BANK]:
                sys.exit(f"ZX02 STREAM CHECK FAILED: resource at raw sector {e[0]}, "
                         f"bank chunk {o // BANK} does not unpack from the disk image")
    mv = lambda e: where[e] if e[1] else e
    table = {p: tuple(mv(e) for e in t) for p, t in table.items()}
    bmp = {n: mv(e) for n, e in bmp.items()}
    snd_table = {p: mv(e) for p, e in snd_table.items()}
    print(f"[zx02] part blob {len(blob)//1024} KB -> {len(out)//1024} KB on disk "
          f"({len(out)/len(blob):.2f}), {len(chunks)} bank streams")
    return bytes(out), table, bmp, snd_table


def main():
    force = "--force" in sys.argv
    # --xex-start N : awgame.xex does NOT start at sector 4 -- it sits after the intro
    #   on the combined disk, so the part blob (and every sector in the emitted table)
    #   is rebased to N + xex_sectors. --no-atr : emit ONLY the table; buildstep_join_disk.py
    #   assembles the actual disk. These two were passed by the build for a long time
    #   but never parsed here, so the table kept the standalone base (4 + xex_sectors)
    #   while the blob really lived after the intro -> the game streamed INTRO bytes as
    #   its bytecode and the VM ran off into the weeds. Now they are honoured, and the
    #   resulting base is written into the .inc (GAME_BLOB_BASE) so buildstep_join_disk.py
    #   can hard-verify the two agree instead of just printing a reminder.
    no_atr = "--no-atr" in sys.argv
    xex_start = 4
    if "--xex-start" in sys.argv:
        xex_start = int(sys.argv[sys.argv.index("--xex-start") + 1])
    if xex_start != 4 and not no_atr:
        sys.exit("--xex-start only makes sense with --no-atr "
                 "(a standalone awgame.atr always puts the xex at sector 4)")

    blob, table, bmp, snd_table, snd_dir, snd_pdir = load_or_build_blob(force)
    blob, table, bmp, snd_table = pack_blob(blob, table, bmp, snd_table)
    open(PACKED_BLOB, "wb").write(blob)

    # bootable layout: boot (sectors 1-3) + awgame.xex (4..) + cached part blob
    boot = open(BOOT, "rb").read()
    if len(boot) > 3 * SECTOR:
        sys.exit(f"boot.bin too big ({len(boot)} > {3*SECTOR})")
    boot = boot.ljust(3 * SECTOR, b"\x00")
    if not os.path.exists(XEX):
        sys.exit(f"{XEX} missing -- build awgame.xex first")
    xex = open(XEX, "rb").read()
    xex = xex.ljust(secs(len(xex)) * SECTOR, b"\x00")
    xex_sectors = len(xex) // SECTOR
    if "--xex-sectors" in sys.argv:     # the disk carries awgame.xex PACKED (pack_xex.py):
        if not no_atr:                  #   build.ps1 passes the packed file's fixed size
            sys.exit("--xex-sectors only makes sense with --no-atr")
        xex_sectors = int(sys.argv[sys.argv.index("--xex-sectors") + 1])
    base = xex_start + xex_sectors  # 1-based ATR sector of the blob's first sector

    if no_atr:
        print(f"xex: {xex_start}-{xex_start+xex_sectors-1}   "
              f"data: {base}-{base+len(blob)//SECTOR-1}   (table only, no awgame.atr)")
    else:
        disk = bytes(boot) + bytes(xex) + blob
        total_sec = len(disk) // SECTOR
        para = (total_sec * SECTOR) // 16
        hdr = bytearray(16)
        struct.pack_into("<H", hdr, 0, 0x0296)
        struct.pack_into("<H", hdr, 2, para & 0xFFFF)
        struct.pack_into("<H", hdr, 4, SECTOR)
        hdr[6] = (para >> 16) & 0xFF
        open(ATR, "wb").write(hdr + disk)
        print(f"boot: 1-3   xex: 4-{3+xex_sectors}   data: {base}-{total_sec}")
        print(f"awgame.atr : {total_sec} sectors ({len(disk)//1024} KB, bootable)")

    # emit the asm sector tables (absolute sector = base + relative; 0 when count==0)
    def sec_col(i):
        return [(base + table[p][i][0]) if table[p][i][1] else 0 for p in PARTS]

    def cnt_col(i):
        return [table[p][i][1] for p in PARTS]

    def arr(name, vals, hi=False):
        b = [(v >> 8) & 0xFF if hi else v & 0xFF for v in vals]
        return f"{name}\n        dta {','.join(str(x) for x in b)}\n"

    pal_s, pal_c = sec_col(0), cnt_col(0)
    cod_s, cod_c = sec_col(1), cnt_col(1)
    v1_s, v1_c = sec_col(2), cnt_col(2)
    v2_s, v2_c = sec_col(3), cnt_col(3)
    with open(INC, "w") as f:
        f.write("; Auto-generated by tools/make_game_atr.py - DO NOT EDIT\n")
        f.write(f"; part index order: {', '.join(str(p) for p in PARTS)}\n")
        f.write(f"GAME_NPARTS = {len(PARTS)}\n")
        f.write(f"GAME_FIRST_PART = {PARTS[0]}\n")
        # every sector below = GAME_BLOB_BASE + relative. buildstep_join_disk.py re-derives
        # the blob's real position from the file sizes and FAILS if it disagrees with
        # this value -- the guard for the rebase bug described in main().
        f.write(f"GAME_BLOB_BASE = {base}\n\n")
        f.write(arr("atr_pal_sec_lo", pal_s)); f.write(arr("atr_pal_sec_hi", pal_s, True))
        f.write(arr("atr_pal_cnt", pal_c))
        f.write(arr("atr_code_sec_lo", cod_s)); f.write(arr("atr_code_sec_hi", cod_s, True))
        f.write(arr("atr_code_cnt_lo", cod_c)); f.write(arr("atr_code_cnt_hi", cod_c, True))
        f.write(arr("atr_v1_sec_lo", v1_s)); f.write(arr("atr_v1_sec_hi", v1_s, True))
        f.write(arr("atr_v1_cnt_lo", v1_c)); f.write(arr("atr_v1_cnt_hi", v1_c, True))
        f.write(arr("atr_v2_sec_lo", v2_s)); f.write(arr("atr_v2_sec_hi", v2_s, True))
        f.write(arr("atr_v2_cnt_lo", v2_c)); f.write(arr("atr_v2_cnt_hi", v2_c, True))
        # background-bitmap table: op_memlist(num) looks num up here; if found, stream
        # the decoded LR page (count sectors) from atr sector -> VRAM page 0.
        bnums = sorted(bmp)
        f.write(f"\nGAME_NBMP = {len(bnums)}\n")
        f.write(arr("atr_bmp_num_lo", [n & 0xFF for n in bnums]))
        f.write(arr("atr_bmp_num_hi", [(n >> 8) & 0xFF for n in bnums]))
        bsec = [base + bmp[n][0] for n in bnums]
        f.write(arr("atr_bmp_sec_lo", bsec)); f.write(arr("atr_bmp_sec_hi", bsec, True))
        f.write(arr("atr_bmp_cnt", [bmp[n][1] for n in bnums]))

        # per-part SFX : blob ATR location (load_sounds streams it to VRAM $11..)
        # + the resId->VRAM directory (op_sound searches the part's slice).
        snd_sec = [(base + snd_table[p][0]) if snd_table[p][1] else 0 for p in PARTS]
        snd_cnt = [snd_table[p][1] for p in PARTS]
        f.write(f"\nSND_NBANK = {len(SND_BLIST)}\n")
        f.write("snd_blist   dta " + ",".join(f"${0x80 | b:02X}" for b in SND_BLIST) + "\n")
        f.write(arr("atr_snd_sec_lo", snd_sec)); f.write(arr("atr_snd_sec_hi", snd_sec, True))
        f.write(arr("atr_snd_cnt_lo", snd_cnt)); f.write(arr("atr_snd_cnt_hi", snd_cnt, True))
        f.write(arr("snd_pdir_start", [snd_pdir[p][0] for p in PARTS]))
        f.write(arr("snd_pdir_cnt", [snd_pdir[p][1] for p in PARTS]))
        f.write(f"SND_DIR_N = {len(snd_dir)}\n")
        f.write(f"SND_AUDF_CAP = {AUDF_CAP}\n")     # every capped entry plays at this AUDF
        f.write(arr("snd_dir_resid", [e[0] & 0xFF for e in snd_dir]))
        f.write(arr("snd_dir_freq", [e[1] for e in snd_dir]))   # $FF = native wildcard
        f.write(arr("snd_dir_blidx", [e[2] for e in snd_dir]))
        f.write(arr("snd_dir_winlo", [e[3] for e in snd_dir]))
        f.write(arr("snd_dir_winhi", [e[4] for e in snd_dir]))
        f.write(arr("snd_dir_lenlo", [e[5] for e in snd_dir]))
        f.write(arr("snd_dir_lenhi", [e[6] for e in snd_dir]))
        # AUDF1 per op_sound freq byte (0..39) -> correct pitch (wildcard entries).
        f.write(arr("snd_audf", AUDF_TAB))
    nsnd = sum(snd_table[p][1] > 0 for p in PARTS)
    print(f"src_game/game_atr.inc : sector tables for {len(PARTS)} parts, "
          f"{len(bmp)} bitmap(s), {len(snd_dir)} sound dir entries ({nsnd} parts w/ SFX)")


if __name__ == "__main__":
    main()
