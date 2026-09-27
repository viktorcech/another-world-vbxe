#!/usr/bin/env python3
"""_gen_segsteps -- prints the two inline edge-step blocks of fill_poly_int (right: j,
left: i). They are the same code on two SMC chains; generated once, pasted into
src_game/aw_raster.asm (the source is the truth, this only avoids a hand-copy typo)."""


def edge(E, chain, idx, a, b, lbl):
    L = lbl
    return f"""        ; ---- {E} edge: dv = pts_x[{a}] - pts_x[{b}], step = dv * recip[hh] (16.16)
        ldx {idx}
        lda pts_xlo,x
        sec
        sbc pts_xlo{'+1' if b.endswith('+1]') else '-1'},x
        tay                         ; Y = dv lo
        lda pts_xhi,x
        sbc pts_xhi{'+1' if b.endswith('+1]') else '-1'},x
        sta dvsign                  ; bit 7 = the sign of dx
        bpl ?{L}ab
        tya                         ; |dx| = -dv lo (|dx| < 256: the low byte is the
        eor #$FF                    ;   whole magnitude)
        tay
        iny
?{L}ab   ldx hh                      ; X = dy
        cpx #1
        jeq fpi_rare.{L}h1
        sty ?{L}a1+1                ; |dx| into both products' square operands
        sty ?{L}a2+1
        sty ?{L}b1+1
        sty ?{L}b2+1
        tya
        eor #$FF
        sta ?{L}a3+1
        sta ?{L}a4+1
        sta ?{L}b3+1
        sta ?{L}b4+1
        ldy recip_lo,x              ; p0 = |dx| * recip_lo
        sec
?{L}a1  lda fmul_sq1l,y
?{L}a3  sbc fmul_sq2l,y
        sta g_N0
?{L}a2  lda fmul_sq1h,y
?{L}a4  sbc fmul_sq2h,y
        sta N1
        ldy recip_hi,x              ; p1 = |dx| * recip_hi
        sec
?{L}b1  lda fmul_sq1l,y
?{L}b3  sbc fmul_sq2l,y
        tax                         ; X = p1 lo
?{L}b2  lda fmul_sq1h,y
?{L}b4  sbc fmul_sq2h,y
        tay                         ; Y = p1 hi
        txa                         ; N = p0 + (p1 << 8)
        clc
        adc N1
        sta N1
        tya
        adc #0
        sta g_N2
        bit dvsign
        jmi fpi_rare.{L}ng
?{L}ps  lda g_N0                    ; step and step*2 in one carry chain
        sta fill_poly_int.smc_{chain}0+1
        asl @
        sta fill_poly_int.smc2_{chain}0+1
        lda N1
        sta fill_poly_int.smc_{chain}1+1
        rol @
        sta fill_poly_int.smc2_{chain}1+1
        lda g_N2
        sta fill_poly_int.smc_{chain}2+1
        rol @
        sta fill_poly_int.smc2_{chain}2+1
        lda #0
        sta fill_poly_int.smc_{chain}3+1
        rol @
        sta fill_poly_int.smc2_{chain}3+1
st_{L}dn
"""


def rare(E, chain, L):
    return f"""; {E} edge, out of line: dy == 1 and the negative steps
{L}h1    bit dvsign                  ; dy == 1 : step = |dx| << 16
        bmi {L}n1
        lda #0                      ; (the same bytes, and C = 0 at the end, as the
        sta fill_poly_int.smc_{chain}0+1    ;   general chain gives)
        sta fill_poly_int.smc2_{chain}0+1
        sta fill_poly_int.smc_{chain}1+1
        sta fill_poly_int.smc2_{chain}1+1
        sta fill_poly_int.smc_{chain}3+1
        sty fill_poly_int.smc_{chain}2+1
        tya
        asl @
        sta fill_poly_int.smc2_{chain}2+1
        lda #0
        rol @
        sta fill_poly_int.smc2_{chain}3+1
        jmp fill_poly_int.st_{L}dn
{L}n1    lda #0
        sta g_N0
        sta N1
        sty g_N2
{L}ng    sec                         ; step = 0 - N (32-bit, borrow-chained)
        lda #0
        sbc g_N0
        sta fill_poly_int.smc_{chain}0+1
        lda #0
        sbc N1
        sta fill_poly_int.smc_{chain}1+1
        lda #0
        sbc g_N2
        sta fill_poly_int.smc_{chain}2+1
        lda #0
        sbc #0
        sta fill_poly_int.smc_{chain}3+1
        lda poly_bcb_h              ; half mode: step*2 too (the full-detail loop
        jeq fill_poly_int.st_{L}dn                  ;   never reads the smc2 chain)
        lda fill_poly_int.smc_{chain}0+1
        asl @
        sta fill_poly_int.smc2_{chain}0+1
        lda fill_poly_int.smc_{chain}1+1
        rol @
        sta fill_poly_int.smc2_{chain}1+1
        lda fill_poly_int.smc_{chain}2+1
        rol @
        sta fill_poly_int.smc2_{chain}2+1
        lda fill_poly_int.smc_{chain}3+1
        rol @
        sta fill_poly_int.smc2_{chain}3+1
        jmp fill_poly_int.st_{L}dn
"""


import sys
if sys.argv[1:] == ['rare']:
    print(rare('right', 'cr', 'r'), end='')
    print(rare('left', 'cl', 'l'), end='')
else:
    print(edge('right', 'cr', 'j_idx', 'j', 'j+1]', 'r').replace('j+1]]', 'j+1]'), end='')
    print(edge('left', 'cl', 'i_idx', 'i', 'i-1]', 'l').replace('i-1]]', 'i-1]'), end='')
