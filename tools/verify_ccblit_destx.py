#!/usr/bin/env python3
"""
verify_ccblit_destx.py - prove (over ALL 16-bit inputs) that the proposed
simplification of cc_blit's dest-X computation is byte-IDENTICAL to the current
code. Fresh tool modelling the CURRENT asm's exact 6502 ops -- not a stale model.

cc_blit (game_cellcache.asm) computes the destination byte column as
    cc_dx = ((dr_x - (dr_x & 1)) >>arith 1) + ax
i.e. it clears dr_x's low bit, then arithmetic-shifts right by 1.

Claim: the "- (dr_x & 1)" is redundant -- an arithmetic >>1 already discards the
low bit, so  (dr_x - (dr_x&1)) >>1  ==  dr_x >>1  for every dr_x. Removing it
saves ~5 instructions (~16 cyc) on every cache HIT, with identical output.

We model BOTH versions with the SAME 6502 primitives the asm uses (16-bit
two's-complement; arithmetic >>1 = `cmp #$80 ; ror hi ; ror lo`) and assert they
match for all 65536 dr_x. The "+ ax" is a common suffix, so equality of the
shift result implies equality of cc_dx.
"""

def arith_shr1_16(v):
    """16-bit arithmetic >>1, exactly as the asm does it:
       A=hi; `cmp #$80` -> C = (hi >= $80) = sign; `ror hi`; `ror lo`."""
    v &= 0xFFFF
    lo, hi = v & 0xFF, (v >> 8) & 0xFF
    c = 1 if hi >= 0x80 else 0          # cmp #$80  -> carry = sign bit
    new_hi = ((c << 7) | (hi >> 1)) & 0xFF
    c = hi & 1                          # carry out of `ror hi`
    new_lo = ((c << 7) | (lo >> 1)) & 0xFF
    return new_lo | (new_hi << 8)

def current(dr_x):
    par = dr_x & 1                      # lda dr_x / and #1
    t = (dr_x - par) & 0xFFFF           # sec / sbc par (16-bit)
    return arith_shr1_16(t)

def proposed(dr_x):
    return arith_shr1_16(dr_x)          # just the >>1, no par subtract

def main():
    bad = 0
    for x in range(0x10000):
        if current(x) != proposed(x):
            bad += 1
            if bad <= 5:
                sx = x - 0x10000 if x >= 0x8000 else x
                print(f"  MISMATCH dr_x={x:#06x} ({sx}): "
                      f"cur={current(x):#06x} new={proposed(x):#06x}")
    if bad == 0:
        print("OK: identical for all 65536 dr_x -> the par-subtract is redundant, "
              "safe to drop.")
    else:
        print(f"FAIL: {bad} mismatches -> NOT safe, keep the par-subtract.")

if __name__ == "__main__":
    main()
