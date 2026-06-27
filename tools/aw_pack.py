#!/usr/bin/env python3
"""
aw_pack.py - Another World resource reader + Bytekiller (Delphine) unpacker.

Step 1 of the Atari/VBXE asset pipeline: read MEMLIST.BIN, locate a resource in
its BANK file, and decompress it. The Bytekiller stream carries a CRC that must
come out 0 when decoded correctly, so unpacking is self-verifying.

Usage:
    python aw_pack.py            # list resources + unpack the intro set
    python aw_pack.py <index>    # dump one resource to out/<index>.bin
"""
import os, struct, sys

_ROOT = os.path.join(os.path.dirname(__file__), "..")
# Original DOS game assets (MEMLIST.BIN + BANK0x). Live in orig/; older runs
# used pc/ -- accept either so the pipeline finds them wherever they sit.
PC_DIR = next((os.path.join(_ROOT, d) for d in ("orig", "pc")
               if os.path.exists(os.path.join(_ROOT, d, "MEMLIST.BIN"))),
              os.path.join(_ROOT, "orig"))
OUT_DIR = os.path.join(_ROOT, "out")

TYPES = {0: "SOUND", 1: "MUSIC", 2: "BITMAP", 3: "PALETTE",
         4: "BYTECODE", 5: "POLY_CINE", 6: "POLY_OBJ"}

# Another World parts: part 16000 = {0x14,0x15,0x16} is the COPY-PROTECTION
# screen; the INTRODUCTION cinematic is part 16001 = {0x17,0x18,0x19}.
INTRO_PALETTE  = 0x17   # 23
INTRO_BYTECODE = 0x18   # 24  (bytecode VM program)
INTRO_POLY     = 0x19   # 25  (cinematic polygons / video1)


def be32(b, o): return struct.unpack_from(">I", b, o)[0]
def be16(b, o): return struct.unpack_from(">H", b, o)[0]


class MemEntry:
    __slots__ = ("idx", "state", "type", "rank", "bank", "offset",
                 "packed", "size")

    def __init__(self, idx, e):
        self.idx    = idx
        self.state  = e[0]
        self.type   = e[1]
        self.rank   = e[6]
        self.bank   = e[7]            # 1 => BANK01
        self.offset = be32(e, 8)      # byte offset inside the bank file
        self.packed = be16(e, 14)     # bytes stored on disk
        self.size   = be16(e, 18)     # bytes after unpacking

    def packed_eq_size(self): return self.packed == self.size


def read_memlist():
    data = open(os.path.join(PC_DIR, "MEMLIST.BIN"), "rb").read()
    out = []
    for i in range(len(data) // 20):
        e = data[i*20:(i+1)*20]
        if e[0] == 0xFF:
            break
        out.append(MemEntry(i, e))
    return out


# ---------------------------------------------------------------------------
# Bytekiller / Delphine unpacker. Input is consumed back-to-front, output is
# written back-to-front. Faithful port of the reference C implementation.
# ---------------------------------------------------------------------------
class Unpacker:
    def __init__(self, src):
        self.src = bytearray(src)
        self.i = len(src) - 4
        self.datasize = self._next_word()          # unpacked length
        self.crc = self._next_word()
        self.bits = self._next_word()
        self.crc ^= self.bits
        self.dst = bytearray(self.datasize)
        self.o = self.datasize - 1

    def _next_word(self):
        v = struct.unpack_from(">I", self.src, self.i)[0]
        self.i -= 4
        return v

    def _next_bit(self):
        carry = self.bits & 1
        self.bits >>= 1
        if self.bits == 0:                          # buffer empty -> refill
            self.bits = self._next_word()
            self.crc ^= self.bits
            carry = self.bits & 1
            self.bits = (self.bits >> 1) | 0x80000000
        return carry

    def _get_bits(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self._next_bit()
        return v

    def _copy_literal(self, nbits, add):
        count = self._get_bits(nbits) + add + 1
        self.datasize -= count
        for _ in range(count):
            self.dst[self.o] = self._get_bits(8)
            self.o -= 1

    def _copy_ref(self, nbits, count):
        self.datasize -= count
        off = self._get_bits(nbits)
        for _ in range(count):
            self.dst[self.o] = self.dst[self.o + off]
            self.o -= 1

    def run(self):
        while self.datasize > 0:
            if not self._next_bit():
                if not self._next_bit():
                    self._copy_literal(3, 0)        # 1..8 raw bytes
                else:
                    self._copy_ref(8, 2)            # 2-byte match, 8-bit off
            else:
                code = self._get_bits(2)
                if code == 3:
                    self._copy_literal(8, 8)        # 9..264 raw bytes
                elif code == 2:
                    self._copy_ref(12, self._get_bits(8) + 1)
                elif code == 1:
                    self._copy_ref(10, 4)
                else:
                    self._copy_ref(9, 3)
        return bytes(self.dst), (self.crc == 0)


def load_resource(me):
    """Return the unpacked bytes for a MemEntry (raw if not packed)."""
    bank = open(os.path.join(PC_DIR, "BANK%02X" % me.bank), "rb").read()
    raw = bank[me.offset:me.offset + me.packed]
    if me.packed_eq_size():
        return raw, True
    return Unpacker(raw).run()


def main():
    mem = read_memlist()
    os.makedirs(OUT_DIR, exist_ok=True)

    if len(sys.argv) > 1:
        idx = int(sys.argv[1], 0)
        me = mem[idx]
        data, ok = load_resource(me)
        path = os.path.join(OUT_DIR, "%02x.bin" % idx)
        open(path, "wb").write(data)
        print(f"#{idx} {TYPES.get(me.type, me.type)} -> {path} "
              f"({len(data)} bytes, crc_ok={ok})")
        return

    print(f"{len(mem)} resources. Unpacking the INTRO set:\n")
    for idx in (INTRO_PALETTE, INTRO_BYTECODE, INTRO_POLY):
        me = mem[idx]
        data, ok = load_resource(me)
        match = "OK" if len(data) == me.size else f"MISMATCH({len(data)})"
        print(f"  #{idx:3} {TYPES.get(me.type,me.type):9} BANK{me.bank:02X} "
              f"@{me.offset:#08x}  {me.packed}->{me.size}  "
              f"crc={'OK' if ok else 'BAD'}  len={match}")
        open(os.path.join(OUT_DIR, "%02x.bin" % idx), "wb").write(data)
    print(f"\nWritten to {os.path.normpath(OUT_DIR)}/")


if __name__ == "__main__":
    main()
