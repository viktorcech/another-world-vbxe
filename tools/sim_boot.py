#!/usr/bin/env python3
"""sim_boot.py - byte-for-byte simulation of src_game/bootloader.asm over the real
awgame_full.atr.

Why: the boot loader that loads BOTH xex files calls SIOV and then NEVER LOOKS AT
THE RESULT (`jsr $E459` / `inc cur_sec`, no check of Y).  In Altirra with
accelerated SIO a read never fails, so the developer never sees it.  On real
hardware behind an SIO2SD a single missed sector is silently accepted and the
loader keeps parsing the XEX stream with 128 stale bytes in the buffer.

This replays the loader exactly, then injects that exact failure mode to show
what it corrupts.

    python sio2sd/sim_boot.py                # verify the clean load
    python sio2sd/sim_boot.py --fault 1234   # pretend sector 1234 read failed
    python sio2sd/sim_boot.py --sweep        # what does a failure at each sector hit?
"""
import os, sys, struct, collections

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ATR = os.path.join(PROJ, 'awgame_full.atr')
SECTOR = 128


class Disk:
    def __init__(self, path):
        d = open(path, 'rb').read()
        magic, paras, self.secsize, parahi = struct.unpack('<HHHB', d[:7])
        assert magic == 0x0296, 'not an ATR'
        self.data = d[16:]
        self.nsec = len(self.data) // SECTOR

    def read(self, sec):                      # sec is 1-based, like DAUX1/2
        o = (sec - 1) * SECTOR
        return self.data[o:o + SECTOR]


class Boot:
    """Exactly bootloader.asm: get_byte / read_sec / parse_seg / data_seg / init / run."""

    def __init__(self, disk, start_sec=4, fail_sectors=()):
        self.d = disk
        self.cur_sec = start_sec
        self.buf = bytearray(SECTOR)          # SECBUF at $0800, whatever was there
        self.buf_pos = SECTOR                 # 128 = force the first read
        self.fail = set(fail_sectors)
        self.mem = bytearray(0x10000)
        self.written = bytearray(0x10000)     # coverage map
        self.segs = []
        self.inits = []
        self.run = None
        self.reads = 0
        self.stale = 0

    def read_sec(self):
        if self.cur_sec in self.fail:
            self.stale += 1                   # SIO error -> loader ignores it, buffer stays
        else:
            self.buf = bytearray(self.d.read(self.cur_sec))
        self.cur_sec += 1
        self.buf_pos = 0
        self.reads += 1

    def get_byte(self):
        if self.buf_pos >= SECTOR:
            self.read_sec()
        b = self.buf[self.buf_pos]
        self.buf_pos += 1
        return b

    def word(self):
        return self.get_byte() | (self.get_byte() << 8)

    def run_loader(self, max_bytes=4_000_000):
        self.get_byte(); self.get_byte()      # skip $FF $FF
        budget = max_bytes
        while budget > 0:
            lo = self.get_byte(); hi = self.get_byte()
            if lo == 0xFF and hi == 0xFF:     # separator -> re-read
                continue
            start = lo | (hi << 8)
            end = self.word()
            if start == 0x02E2:               # INITAD
                a = self.word()
                self.inits.append(a)
                continue
            if start == 0x02E0:               # RUNAD
                self.run = self.word()
                return 'RUN'
            n = ((end - start) & 0xFFFF) + 1
            self.segs.append((start, end, n))
            for i in range(n):
                p = (start + i) & 0xFFFF
                self.mem[p] = self.get_byte()
                self.written[p] = 1
                budget -= 1
            if budget <= 0:
                return 'BUDGET'
        return 'BUDGET'


def summarise(b, label):
    print(f"--- {label}")
    print(f"  sektorov precitanych : {b.reads}  (sektory {4}..{b.cur_sec-1})")
    print(f"  segmentov            : {len(b.segs)}   INIT: {len(b.inits)}   RUN: "
          f"{'$%04X' % b.run if b.run else 'NONE'}")
    ram = [s for s in b.segs if not (0x4000 <= s[0] < 0x8000)]
    win = [s for s in b.segs if 0x4000 <= s[0] < 0x8000]
    print(f"  RAM segmentov        : {len(ram)}  ({sum(s[2] for s in ram)} B)")
    for s, e, n in ram:
        print(f"      ${s:04X}-${e:04X}  {n:6} B")
    print(f"  VRAM stream (MEMAC-B $4000-$7FFF): {len(win)} segmentov, "
          f"{sum(s[2] for s in win)} B  -> INIT po kazdom")
    return ram


def main():
    disk = Disk(ATR)
    print(f"ATR: {disk.nsec} sektorov x {disk.secsize} B "
          f"({disk.nsec*SECTOR/1024/1024:.2f} MB)\n")

    b = Boot(disk)
    r = b.run_loader()
    ram = summarise(b, f"awintro.xex (od sektora 4) -> {r}")
    intro_end = b.cur_sec

    if '--sweep' in sys.argv:
        print("\n--- sweep: co zasiahne vypadok jedneho sektora (intro) ---")
        hits = collections.Counter()
        for sec in range(4, intro_end):
            bb = Boot(disk, 4, fail_sectors=(sec,))
            try:
                bb.run_loader()
            except Exception:
                hits['CRASH / neparsovatelne'] += 1
                continue
            if bb.run != b.run:
                hits['RUN adresa zmenena -> skok do neznama'] += 1
                continue
            if len(bb.segs) != len(b.segs):
                hits['rozpadnuty segmentovy retazec (chybna hlavicka segmentu)'] += 1
                continue
            if bytes(bb.mem) == bytes(b.mem):
                hits['bez ucinku na RAM (zasiahnuty len VRAM stream)'] += 1
                continue
            lo = next(i for i in range(0x10000) if bb.mem[i] != b.mem[i])
            hits[f'poskodena RAM v okoli ${lo & 0xFF00:04X}'] += 1
        for k, v in hits.most_common():
            print(f"   {v:5} sektorov -> {k}")

    for a in sys.argv:
        if a.startswith('--fault'):
            pass
    if '--fault' in sys.argv:
        sec = int(sys.argv[sys.argv.index('--fault') + 1], 0)
        bb = Boot(disk, 4, fail_sectors=(sec,))
        rr = bb.run_loader()
        print(f"\n--- vypadok sektora {sec} -> {rr}")
        print(f"  segmentov {len(bb.segs)} (cisty beh {len(b.segs)}), "
              f"RUN ${bb.run:04X}" if bb.run else "  RUN: NONE")
        diff = [i for i in range(0x10000) if bb.written[i] and bb.mem[i] != b.mem[i]]
        print(f"  poskodenych bajtov v RAM: {len(diff)}"
              + (f"  (${min(diff):04X}-${max(diff):04X})" if diff else ""))

    # --- timing ---------------------------------------------------------
    print("\n--- cas nacitania (jeden sektor = 128 B + hlavicky) ---")
    for name, sps in (('stock SIO 19200 b/s', 38), ('SIO2SD high speed, div 6 (~68 kb/s)', 128)):
        t = b.reads / sps
        print(f"   {name:38} {t:6.1f} s pre intro ({b.reads} sektorov)")
    print(f"   pozn.: Altirra s akcelerovanym SIO to ma okamzite -> na PC sa chyba neprejavi")


if __name__ == '__main__':
    main()


# ---------------------------------------------------------------------------
# extra checks (appended): chain to the game xex + memory-map hazards
# ---------------------------------------------------------------------------
HAZARDS = [
    (0x0000, 0x00FF, 'zero page (OS + SIO pouzivaju $00-$7F)'),
    (0x0200, 0x03FF, 'OS vektory / DCB - SIOV ich prepisuje'),
    (0x0700, 0x07FF, 'REZIDENTNY boot loader (chain do hry by sa rozbil!)'),
    (0x0800, 0x087F, 'SECBUF boot loadera'),
    (0x8000, 0x8FFF, 'MEMAC-A okno VBXE (RAM je tam skryta pocas VBXE zapisov)'),
    (0xA000, 0xBFFF, 'BASIC ROM / KARTRIDZ: s vlozenym cartridgom (RD5) je RAM ODPOJENA'),
    (0xC000, 0xFFFF, 'OS ROM'),
]


def hazard_report(segs, label):
    print(f"\n--- hazardy pamatovej mapy: {label}")
    hit = False
    for s, e, n in segs:
        for lo, hi, why in HAZARDS:
            if s <= hi and e >= lo:
                print(f"   ${s:04X}-${e:04X} ({n} B) zasahuje ${lo:04X}-${hi:04X}: {why}")
                hit = True
    if not hit:
        print("   ziadny segment nekolidiuje")


def chain_check():
    disk = Disk(ATR)
    b = Boot(disk); b.run_loader()
    game_sec = b.cur_sec
    print(f"\n--- chain: intro skoncilo na sektore {b.cur_sec-1}, hra ma zacat na {game_sec}")
    s = disk.read(game_sec)
    print(f"   sektor {game_sec} zacina: {s[:4].hex()}  "
          f"({'OK - $FF $FF XEX hlavicka' if s[0] == 0xFF and s[1] == 0xFF else 'CHYBA - nie je to XEX!'})")
    g = Boot(disk, game_sec)
    r = g.run_loader()
    ram = [x for x in g.segs if not (0x4000 <= x[0] < 0x8000)]
    win = [x for x in g.segs if 0x4000 <= x[0] < 0x8000]
    print(f"   awgame.xex -> {r}, RUN ${g.run:04X}, {g.reads} sektorov "
          f"({game_sec}..{g.cur_sec-1})")
    print(f"   RAM segmentov {len(ram)} ({sum(x[2] for x in ram)} B), "
          f"VRAM stream {len(win)} ({sum(x[2] for x in win)} B)")
    for s_, e_, n_ in ram:
        if n_ > 16:
            print(f"      ${s_:04X}-${e_:04X}  {n_:6} B")
    hazard_report([x for x in b.segs if x[2] > 16], 'awintro.xex')
    hazard_report([x for x in g.segs if x[2] > 16], 'awgame.xex')
    return b, g


if __name__ == '__main__' and '--chain' in sys.argv:
    chain_check()


# ---------------------------------------------------------------------------
# model of the FIXED loader: SIO_TRIES attempts per sector, hard stop on failure
# ---------------------------------------------------------------------------
SIO_TRIES = 4


class LoadError(Exception):
    pass


class BootFixed(Boot):
    """`fail_counts[sector]` = how many attempts in a row fail for that sector."""

    def __init__(self, disk, start_sec=4, fail_counts=None):
        super().__init__(disk, start_sec)
        self.fail_counts = dict(fail_counts or {})
        self.retries = 0

    def read_sec(self):
        left = self.fail_counts.get(self.cur_sec, 0)
        tries = SIO_TRIES
        while tries:
            if left:
                left -= 1
                tries -= 1
                self.retries += 1
                continue
            self.buf = bytearray(self.d.read(self.cur_sec))
            self.cur_sec += 1
            self.buf_pos = 0
            self.reads += 1
            return
        raise LoadError(f'DISK ERROR SEC=${self.cur_sec:04X} po {SIO_TRIES} pokusoch')


def verify_fix():
    disk = Disk(ATR)
    print(f"ATR: {disk.nsec} sektorov\n")

    old = Boot(disk); old.run_loader()
    new = BootFixed(disk); new.run_loader()
    same = bytes(old.mem) == bytes(new.mem) and old.run == new.run and old.reads == new.reads
    print(f"1) bez chyby        : identicky vysledok ako predtym? {'ANO' if same else 'NIE'}"
          f"  ({new.reads} sektorov, RUN ${new.run:04X})")

    b = BootFixed(disk, fail_counts={100: 3, 500: 1, 2000: 2})
    b.run_loader()
    ok = bytes(b.mem) == bytes(old.mem)
    print(f"2) 3 chybne sektory, kazdy sa podari do {SIO_TRIES} pokusov: "
          f"{'nacitane SPRAVNE' if ok else 'POSKODENE'}  ({b.retries} opakovani)")

    b = BootFixed(disk, fail_counts={1234: SIO_TRIES})
    try:
        b.run_loader()
        print("3) sektor zlyha vzdy: BEZ CHYBY - to je zle!")
    except LoadError as e:
        print(f"3) sektor zlyha vzdy: zastavene s hlaskou -> {e}")

    b = Boot(disk, 4, fail_sectors=(1234,))
    b.run_loader()
    diff = sum(1 for i in range(0x10000) if b.written[i] and b.mem[i] != old.mem[i])
    print(f"   (stary loader na tom istom sektore: pokracoval dalej, "
          f"{diff} poskodenych bajtov v RAM, ziadna hlaska)")


if __name__ == '__main__' and '--verify-fix' in sys.argv:
    verify_fix()
