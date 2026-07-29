#!/usr/bin/env python3
"""
cyc_hotpath.py - cycle-EXACT cost of the GAME rasterizer inner loops.

Built from scratch (2026-07-04). Does NOT reuse perf_model.py / sim_atari.py or any
of their hand-guessed C_* constants. Every number here is a documented 6502 cycle
count applied to the REAL instruction stream transcribed from:
    src_game/aw_raster.asm     (fill_poly_int ?row loop, draw_scanline*, add_steps)
    src_game/aw_polygon.asm    (calc_step, fmul_seta/fmul_b, rs_z4)
    src/aw_vbxe.asm            (fill_span / fire_fill)
Addressing modes taken from src/aw_equates.inc + src_game/game_zp.inc:
    ZP  ($C0-$C7 cr/cl, $8C-$91 scol/sx/sy/slen, $D0-$D3 a/b)  -> lda/adc/sta = 3
    ABS (RAMB+  hy_lo/hy_hi/row_cnt/N0-3/hh/dv/poly_bcb_h/hires) -> lda/adc = 4,
        sta = 4, inc/dec = 6              <-- perf_model.py got several of these wrong
No frequency data is read from the old sim: costs are reported PER EVENT
(per scanline / per edge / per coord) and candidates are ranked by cyc/event and
by share of the per-scanline body (the innermost, most-frequent loop).

    python tools/cyc_hotpath.py
"""

# ---- 6502 cycle table (NMOS, no decimal) ------------------------------------
# branch: 2 not-taken, 3 taken (+1 page cross, ignored - steady-state same page).
BR_T, BR_NT = 3, 2
C = {
    ('lda', 'imm'): 2, ('lda', 'zp'): 3, ('lda', 'abs'): 4, ('lda', 'absx'): 4,
    ('lda', 'absx_pc'): 5, ('lda', 'indy'): 5,
    ('sta', 'zp'): 3, ('sta', 'abs'): 4, ('sta', 'absx'): 5,
    ('adc', 'imm'): 2, ('adc', 'zp'): 3, ('adc', 'abs'): 4, ('adc', 'absx'): 4,
    ('sbc', 'imm'): 2, ('sbc', 'zp'): 3, ('sbc', 'abs'): 4, ('sbc', 'absx'): 4,
    ('cmp', 'imm'): 2, ('cmp', 'zp'): 3, ('cmp', 'abs'): 4,
    ('and', 'imm'): 2, ('eor', 'imm'): 2, ('ora', 'zp'): 3, ('ora', 'abs'): 4,
    ('inc', 'zp'): 5, ('inc', 'abs'): 6, ('dec', 'zp'): 5, ('dec', 'abs'): 6,
    ('ldx', 'imm'): 2, ('ldx', 'zp'): 3, ('ldx', 'abs'): 4,
    ('ldy', 'imm'): 2, ('ldy', 'zp'): 3, ('ldy', 'abs'): 4,
    ('asl', 'acc'): 2, ('lsr', 'acc'): 2, ('ror', 'acc'): 2, ('rol', 'acc'): 2,
    ('asl', 'zp'): 5, ('lsr', 'zp'): 5, ('ror', 'zp'): 5, ('rol', 'zp'): 5,
    ('sec', None): 2, ('clc', None): 2, ('tax', None): 2, ('tay', None): 2,
    ('txa', None): 2, ('tya', None): 2, ('pha', None): 3, ('pla', None): 4,
    ('jmp', 'abs'): 3, ('jsr', None): 6, ('rts', None): 6,
}


def s(*instrs):
    """Sum a stream. Each item: (op, mode) or ('br', taken_bool) or int (literal)."""
    t = 0
    for i in instrs:
        if isinstance(i, int):
            t += i
        elif i[0] == 'br':
            t += BR_T if i[1] else BR_NT
        else:
            t += C[i]
    return t


def add_chain(byte_op_cyc):
    """4-byte accumulate: lda zp / <step> / sta zp, x4, per edge chain."""
    return sum(byte_op_cyc)


# =============================================================================
# ROUTINES  (transcribed 1:1; asm line refs in comments)
# =============================================================================

# --- add_steps : the 16.16 edge DDA, inline in ?row (aw_raster.asm 171-196) ---
#   clc ; {lda crN(zp) ; adc #imm ; sta crN(zp)} x4 ; clc ; {...cl...} x4
_add1_byte = s(('lda', 'zp'), ('adc', 'imm'), ('sta', 'zp'))          # 8
ADD_STEPS_1X = s(('clc', None)) + 4 * _add1_byte + s(('clc', None)) + 4 * _add1_byte

# --- ?row loop tail : hy++ and the y-bound dispatch (aw_raster.asm 197-212) ---
#   FAST clip (smc_yj patched to `jmp yk_row`, y-test skipped):
ROW_TAIL_FAST = s(
    ('inc', 'abs'), ('br', True),        # inc hy_lo ; bne ?hyok (taken 255/256)
    ('jmp', 'abs'),                      # smc_yj jmp yk_row
    ('dec', 'abs'), ('br', True),        # dec row_cnt ; bne ?row
)
#   FULL clip (smc_yj = yk_tst, the per-row y bounds test, aw_raster.asm 205-210):
ROW_TAIL_YTEST = s(
    ('inc', 'abs'), ('br', True),
    ('jmp', 'abs'),                      # smc_yj jmp yk_tst
    ('lda', 'abs'), ('br', False),       # lda hy_hi ; bmi (nt)
    ('br', False),                       # bne ?retall (nt)
    ('lda', 'abs'), ('cmp', 'imm'), ('br', False),   # lda hy_lo; cmp #SCRH; bcs (nt)
    ('dec', 'abs'), ('br', True),
)

# --- draw_scanline_fast body (aw_raster.asm 443-469), one ordering branch -----
DSL_FAST = s(
    ('lda', 'abs'), ('sta', 'zp'),                       # lda hy_lo ; sta sy
    ('sec', None),
    ('lda', 'zp'), ('sbc', 'zp'), ('lda', 'zp'), ('sbc', 'zp'),  # cl2-cr2 / cl3-cr3
    ('br', False),                                       # bcc ?xll
    # order a/b : 4x (lda zp ; sta zp)
    4 * s(('lda', 'zp'), ('sta', 'zp')),
    ('jmp', 'abs'),                                      # jmp emit_span
)

# --- draw_scanline full (aw_raster.asm 359-426): y-test + up to 2 clip tests --
DSL_FULL = s(
    ('lda', 'abs'), ('br', True),          # lda hy_hi ; beq ?inr1 (in-range)
    ('lda', 'abs'), ('cmp', 'imm'), ('br', True),   # lda hy_lo; cmp #SCRH; bcc
    ('sta', 'zp'),                         # sta sy
    ('sec', None),
    ('lda', 'zp'), ('sbc', 'zp'), ('lda', 'zp'), ('sbc', 'zp'), ('br', False),
    4 * s(('lda', 'zp'), ('sta', 'zp')),   # order a/b
    ('jmp', 'abs'),                        # jmp ?clip (same-page)
    # ?clip a/b range tests, typical: a in range, b in range (aw_raster 395-424)
    ('lda', 'zp'), ('cmp', 'imm'), ('br', True),    # a_hi<$81 -> ?aok
    ('lda', 'zp'), ('cmp', 'imm'), ('br', False),   # b_hi>=$80
    ('lda', 'zp'), ('cmp', 'imm'), ('br', True),    # a_hi>=$80 -> ?bclip
    ('lda', 'zp'), ('cmp', 'imm'), ('br', True),    # b_hi<$81 -> ?ready
    ('jmp', 'abs'),                        # jmp emit_span
)

# --- emit_span, LR path, HIRES_CAP build (aw_vbxe.asm via emit_span 476-540) --
EMIT_LR = s(
    ('lda', 'abs'), ('br', False),         # lda hires ; bne ?sr  (LR: not taken)
    ('lsr', 'zp'), ('ror', 'zp'),          # a>>1
    ('lda', 'zp'), ('sta', 'zp'), ('lda', 'zp'), ('sta', 'zp'),   # sx_lo/sx_hi
    ('lsr', 'zp'), ('ror', 'zp'),          # b>>1
    ('lda', 'zp'), ('sec', None), ('sbc', 'zp'), ('sta', 'zp'),   # slen_lo=b-a
    ('lda', 'imm'), ('sta', 'zp'),         # slen_hi=0
    ('jmp', 'abs'),                        # jmp ?col
    ('jmp', 'abs'),                        # cc_fsp jmp fill_span
)

# --- fill_span, LR + cached-colour + blitter-idle-hit (aw_vbxe.asm 426-511) ---
FILL_SPAN = s(
    ('ldx', 'zp'),                         # ldx sy
    ('ldy', 'abs'), ('br', True),          # ldy hires ; beq ?lrlut (LR)
    ('lda', 'absx'), ('clc', None), ('adc', 'zp'), ('sta', 'zp'),  # dlo = row_lo[sy]+sx
    ('lda', 'absx'), ('adc', 'zp'), ('sta', 'zp'),                 # dmid = row_hi[sy]+sx_hi
    ('lda', 'abs'), ('br', False),         # ?bw lda BL_BUSY ; bne (idle -> not taken)
    ('lda', 'zp'), ('sta', 'abs'),         # DST_ADDR   = dlo
    ('lda', 'zp'), ('sta', 'abs'),         # DST_ADDR+1 = dmid
    ('lda', 'zp'), ('sta', 'abs'),         # WIDTH   = slen_lo
    ('lda', 'zp'), ('sta', 'abs'),         # WIDTH+1 = slen_hi
    ('lda', 'zp'), ('cmp', 'imm'), ('br', False),   # scol ; cmp #$11 ; bcs ?copy (nt)
    ('cmp', 'abs'), ('br', True),          # cmp last_scol ; beq ?fire (cached)
    ('lda', 'imm'), ('sta', 'abs'),        # ?fire BL_START = 1
    ('rts', None),
)

# --- calc_step, the mul path (aw_polygon.asm 341-426) : per EDGE ---------------
FMUL_SETA = s(('sta', 'abs'), ('sta', 'abs'), ('eor', 'imm'),
              ('sta', 'abs'), ('sta', 'abs'), ('rts', None))   # patches 4 operands
# NOTE: fmlb operands are SMC low-bytes of a page-aligned abs,x base -> lda/sbc abs,x=4
FMUL_B = s(('sec', None),
           ('lda', 'absx'), ('sbc', 'absx'), ('sta', 'zp'),    # qp_lo (qp in ZP? see below)
           ('lda', 'absx'), ('sbc', 'absx'), ('sta', 'zp'),    # qp_hi
           ('rts', None))

CALC_STEP_SETUP = s(
    ('sta', 'abs'),                        # stx ?xsv  (abs self-store)
    ('lda', 'imm'), ('sta', 'abs'),        # dvsign = 0
    ('lda', 'abs'), ('br', True),          # lda dv_hi ; bpl ?abs (dx>=0 common) -> skip neg
    ('lda', 'abs'), ('cmp', 'imm'), ('br', False),   # ?abs lda hh ; cmp #1 ; bne ?mul
)
CALC_STEP_MUL = s(
    ('lda', 'abs'),                        # lda dv_lo
    6,                                     # jsr fmul_seta (6) ... routine body sep
) + FMUL_SETA + s(
    ('ldx', 'abs'), ('lda', 'absx'), ('tax', None),  # ldx hh; lda recip_lo,x; tax
    6,                                     # jsr fmul_b
) + FMUL_B + s(
    ('lda', 'zp'), ('sta', 'abs'), ('lda', 'zp'), ('sta', 'abs'),   # N0=qp_lo,N1=qp_hi
    ('ldx', 'abs'), ('lda', 'absx'), ('tax', None),  # ldx hh; lda recip_hi,x; tax
    6,
) + FMUL_B + s(
    ('lda', 'abs'), ('clc', None), ('adc', 'zp'), ('sta', 'abs'),   # N1 += qp_lo
    ('lda', 'zp'), ('adc', 'imm'), ('sta', 'abs'),                  # N2 = qp_hi + carry
)
# write-out (positive branch, aw_polygon.asm 385-396): 4x (lda N ; sta smc+1,x)
CALC_STEP_WR = s(
    ('ldx', 'abs'), ('lda', 'abs'), ('br', False),   # ldx ?xsv; lda dvsign; bne ?neg (nt)
    s(('lda', 'abs'), ('sta', 'absx')),              # N0 -> smc_cr0+1,x
    s(('lda', 'abs'), ('sta', 'absx')),              # N1
    s(('lda', 'abs'), ('sta', 'absx')),              # N2
    s(('lda', 'imm'), ('sta', 'absx')),              # 0 -> smc_cr3
    ('br', True),                                    # beq ?dbl (A=0 always)
)
# ?dbl doubling pass for the half-detail paired loop (aw_polygon.asm 410-423):
CALC_STEP_DBL = s(
    ('lda', 'abs'), ('br', False),                   # lda poly_bcb_h ; beq ?ret (half: nt)
    4 * s(('lda', 'absx'), ('asl', 'acc'), ('sta', 'absx')),   # smc<<1 -> smc2 (rol after 1st)
) + s(('rts', None))

# --- read_scaled coord paths (aw_polygon.asm) ---------------------------------
RS_FAST = s(('ldy', 'imm'), ('lda', 'indy'), ('inc', 'zp'), ('br', True),  # rs_fast 212-220
            ('sta', 'zp'), ('lda', 'imm'), ('sta', 'zp'), ('rts', None)) + s(('jmp', 'abs'))
# rs_z4 : two table multiplies per coord (aw_polygon.asm 245-271), no branch to ?w
RS_Z4 = s(
    ('ldy', 'imm'), ('lda', 'indy'), ('inc', 'zp'), ('br', True),  # inlined fetch
    ('tay', None), ('sec', None),
    ('lda', 'absx'), ('sbc', 'absx'), ('sta', 'zp'),               # p2 = z4_hi*m  (fzh)
    ('lda', 'absx'), ('sbc', 'absx'), ('sta', 'zp'),
    ('sec', None),
    ('lda', 'absx'), ('sbc', 'absx'),                              # p1 = z4_lo*m  (fzl)
    ('lda', 'absx'), ('sbc', 'absx'),
    ('clc', None), ('adc', 'zp'), ('sta', 'zp'), ('br', False),    # scaled = p2+hi(p1)
    ('rts', None),
) + s(('jmp', 'abs'))   # via read_scaled rs_smc jmp


# =============================================================================
# COMPOSE : one drawn scanline, full-detail, fast clip, LR, cached colour
# =============================================================================
JSR = C[('jsr', None)]

PER_SCANLINE = {
    'jsr draw_scanline':   JSR,
    'draw_scanline_fast':  DSL_FAST,
    'emit_span (LR)':      EMIT_LR,
    'fill_span (BCB+fire)': FILL_SPAN,   # includes the returning rts
    'add_steps (16.16 DDA)': ADD_STEPS_1X,
    '?row tail (hy/yk/cnt)': ROW_TAIL_FAST,
}
SCAN_TOTAL = sum(PER_SCANLINE.values())

# per edge (segment): the slope compute written straight into the SMC operands
PER_EDGE_FULL = CALC_STEP_SETUP + CALC_STEP_MUL + CALC_STEP_WR + CALC_STEP_DBL
PER_EDGE_NODBL = CALC_STEP_SETUP + CALC_STEP_MUL + CALC_STEP_WR + \
    s(('lda', 'abs'), ('br', True), ('rts', None))   # poly_bcb_h=0 -> ?ret immediately


def bar(v, tot, width=34):
    n = round(width * v / tot)
    return '#' * n + '.' * (width - n)


def a16_report():
    print('\n' + '=' * 70)
    print(' RAPIDUS NATIVE 65816 (A16) MODEL  -- DDA / per-scanline math in 16-bit acc')
    print('=' * 70)
    print(' (Game currently feeds Rapidus 6502-EMULATION code; it detects Rapidus only')
    print('  to pick full vs half detail. No REP/native-mode in src_game. This models')
    print('  what native .A16 would cost for the SAME logic, same Rapidus clock.)\n')

    print(f' add_steps (the 16.16 DDA, my #2 hot spot):')
    print(f'   6502-emul : {ADD_STEPS_1X:3d} cyc  (2 clc + 8x [lda dp;adc #imm;sta dp])')
    print(f'   native A16: {ADD_STEPS_A16:3d} cyc  (2 clc + 4x 16-bit [lda;adc #imm16;sta])'
          f'  = -{ADD_STEPS_1X-ADD_STEPS_A16} ({100*(ADD_STEPS_1X-ADD_STEPS_A16)/ADD_STEPS_1X:.0f}%)')

    sy_set = s(('lda', 'abs'), ('sta', 'zp'))            # hy->sy byte poke, unchanged
    # ---- three scopes, per drawn scanline (full-detail, fast clip, LR) ----
    s1_add = TOGGLE + ADD_STEPS_A16                      # add_steps only, rep/sep wrapped
    S1 = SCAN_TOTAL - ADD_STEPS_1X + s1_add
    # moderate: one 16-bit region over the whole ?row body; byte pokes in a bracket
    S2 = (JSR + (DSL_FAST_A16 + sy_set) + EMIT_LR_A16 + FILL_SPAN_A16
          + ADD_STEPS_A16 + ROW_TAIL_FAST + TOGGLE)
    # aggressive == moderate assuming C1 (hires SMC) already applied AND a word row LUT
    # (both folded into the A16 routine costs above); same figure, noted separately.

    def line(tag, tot, note):
        d = SCAN_TOTAL - tot
        print(f'   {tot:3d} cyc/scanline   -{d:3d} ({100*d/SCAN_TOTAL:4.1f}%)   {tag}')
        print(f'                                    {note}')

    print(f'\n per DRAWN scanline (baseline 6502-emul = {SCAN_TOTAL} cyc):')
    line('[A16-1] add_steps only, REP/SEP-wrapped', S1,
         'bit-exact, smallest change, no word-LUT needed')
    line('[A16-2] whole ?row math native (add_steps+dsl+emit+fill)', S2,
         'needs: native-mode region, 16-bit word row-LUT (+400B), C1 hires-SMC')
    print(f'\n   note: A16 wins ONLY the CPU arithmetic. The fill_span 8-bit VBXE pokes')
    print(f'   ({FILL_SPAN_8BIT_TAIL} cyc: BL_BUSY/scol/BL_START) and the blitter itself do NOT')
    print(f'   speed up (VBXE bus stays native ~1.77MHz regardless of CPU mode).')
    print(f'   These are instruction-stream cycle counts; on Rapidus both emul and')
    print(f'   native run at the same clock, so the ratio IS the wall-clock ratio.')


# =============================================================================
# RAPIDUS NATIVE 65816 (A16) MODEL  -- how much the DDA / per-scanline arithmetic
# would save run in native 16-bit-accumulator mode (REP #$20) instead of the
# current 6502-emulation code the game feeds Rapidus.
#
# Native-mode cycle counts (65C816, m=0 16-bit acc, x=0, DIRECT-PAGE reg D=$0000
# so DL=0 -> no dp penalty; the game's cr/cl live at $C0-$C7 = dp). Rule: a 16-bit
# memory transfer costs the 6502 count +1 (dp) / +1 (abs) / +1 (imm); RMW +2.
# Both emulation and native code run at the SAME Rapidus clock, so the ratio of
# these counts is the real wall-clock ratio on the Rapidus path.
# =============================================================================
A16 = {
    ('lda', 'dp'): 4, ('sta', 'dp'): 4, ('adc', 'dp'): 4, ('sbc', 'dp'): 4,
    ('cmp', 'dp'): 4, ('lda', 'imm'): 3, ('adc', 'imm'): 3, ('cmp', 'imm'): 3,
    ('lda', 'abs'): 5, ('sta', 'abs'): 5, ('lda', 'absx'): 5, ('sta', 'absx'): 6,
    ('lsr', 'acc'): 2, ('ror', 'acc'): 2, ('asl', 'acc'): 2,
    ('clc', None): 2, ('sec', None): 2, ('tay', None): 2, ('tax', None): 2,
    ('jmp', 'abs'): 3, ('jsr', None): 6, ('rts', None): 6,
    ('rep', None): 3, ('sep', None): 3, ('br_t'): 3, ('br_nt'): 2,
}


def a(*instrs):
    t = 0
    for i in instrs:
        if isinstance(i, int):
            t += i
        elif i[0] == 'br':
            t += A16['br_t'] if i[1] else A16['br_nt']
        else:
            t += A16[i]
    return t


TOGGLE = A16[('rep', None)] + A16[('sep', None)]   # REP+SEP bracket = 6

# --- add_steps in A16: cr/cl are 16.16 = two 16-bit words; add a 32-bit step ---
#   clc ; lda cr0(16);adc #lo16;sta cr0(16) ; lda cr2(16);adc #hi16;sta cr2(16) ; (x cl)
_a16_word = a(('lda', 'dp'), ('adc', 'imm'), ('sta', 'dp'))          # 11
ADD_STEPS_A16 = a(('clc', None)) + 2 * _a16_word + a(('clc', None)) + 2 * _a16_word  # 48

# --- draw_scanline_fast arithmetic in A16 (16-bit compare + 16-bit endpoint order);
#     sy-set stays a byte poke (kept at its 6502 cost, counted in the 8-bit bracket) --
DSL_FAST_A16 = (
    a(('lda', 'dp'), ('cmp', 'dp'), ('br', False))      # cl(16) cmp cr(16) ; bcc
    + a(('lda', 'dp'), ('sta', 'dp')) * 2               # order a,b (two 16-bit moves)
    + a(('jmp', 'abs')))

# --- emit_span LR in A16 (assumes C1 already removed the per-span hires test):
#     a>>1 and b>>1 and slen=b-a all 16-bit --
EMIT_LR_A16 = (
    a(('lda', 'dp'), ('lsr', 'acc'), ('sta', 'dp'))     # sx = a>>1  (16-bit)
    # b>>1 then slen = (b>>1) - sx : lda b(16);lsr;sec;sbc sx(16);sta slen(16)
    + a(('lda', 'dp'), ('lsr', 'acc'), ('sec', None), ('sbc', 'dp'), ('sta', 'dp'))
    + a(('jmp', 'abs'), ('jmp', 'abs')))                # ?col + fill_span

# --- fill_span in A16: row-offset add needs a 16-bit (word) row LUT; DST + WIDTH
#     written as single 16-bit stores; the VBXE 8-bit pokes stay 8-bit (bracket) --
FILL_SPAN_A16_16BIT = (
    a(('tay', None))                                    # (sy already in a reg)
    + a(('lda', 'absx'), ('clc', None), ('adc', 'dp'), ('sta', 'dp'))  # dst = row16[sy]+sx
    + a(('lda', 'dp'), ('sta', 'abs'))                  # BCB_DST = dst (16-bit)
    + a(('lda', 'dp'), ('sta', 'abs')))                 # BCB_WIDTH = slen (16-bit)
# the 8-bit tail of fill_span (BL_BUSY poll, scol cache cmp, BL_START, rts): reuse
# the measured 6502 cost of those exact instructions (they don't change in native):
FILL_SPAN_8BIT_TAIL = s(
    ('lda', 'abs'), ('br', False),                      # BL_BUSY poll
    ('lda', 'zp'), ('cmp', 'imm'), ('br', False),       # scol ; cmp #$11
    ('cmp', 'abs'), ('br', True),                       # cmp last_scol ; beq
    ('lda', 'imm'), ('sta', 'abs'),                     # BL_START
    ('rts', None))
FILL_SPAN_A16 = FILL_SPAN_16BIT = FILL_SPAN_A16_16BIT + FILL_SPAN_8BIT_TAIL


# =============================================================================
# math.md TECHNIQUE BAKE-OFF  -- evaluate the skill's OWN multiply/divide menu
# against the game's real hot path. Cycle figures are the MD's authoritative
# numbers (algorithms/math/multiplication.md table + prose), cross-checked
# against the game's counted fmul_b body.
# =============================================================================
MD_MUL = {                       # cycles per unsigned 8x8->16, from math.md
    'fmulu  (Fox/Tqa, 4x512B LUT)  [10.11]': 14,     # <- the game's choice
    'mult_fast (511+256 LUT)       [10.1]':  45,
    'QS quarter-square (1x512B LUT)[10.5]':  29,
    'Keldon 512B LUT               [10.1]':  29,
    'smallest 8x8 (16B, no table)  [10.1]': 1545,
}
# The game's fmul_b BODY as counted here (sec + 2x{lda,sbc,lda,sbc} + 2 sta),
# i.e. the fmulu core plus storing qp to ZP (the MD's 14 is register-only A:Y):
FMUL_B_CORE = FMUL_B - C[('rts', None)]

# calc_step / rs_z4 / mul_zoom each need TWO 8x8 (an 8x16 product):
MULS_PER_EDGE = 2

# What calc_step REPLACED: a real variable |dx|<<16 / dy divide. Reference cost of
# a standard 16-iteration restoring-divide loop (no MD constant-divisor shortcut
# applies -- dy is a runtime variable). Per-iter avg, counted from the 6502 idiom:
#   asl quot_lo(5) rol quot_hi(5) rol rem(5) lda rem(3) sec(2) sbc dy(3)
#   bcc skip(2.5 avg) [0.5x: sta rem(3)+inc quot(5)] dex(2) bne(3)
DIV16_REF = 16 * (5 + 5 + 5 + 3 + 2 + 3 + 2.5 + 0.5 * (3 + 5) + 2 + 3)
RECIP_SIMPLE = MULS_PER_EDGE * FMUL_B_CORE   # the 2 core fmulu bodies calc_step runs


def md_bakeoff():
    print('\n' + '=' * 70)
    print(' math.md BAKE-OFF  -- the skill\'s multiply/divide menu vs the game')
    print('=' * 70)
    print('\n 8x8->16 MULTIPLY (calc_step, rs_z4 and mul_zoom each need 2 of these):')
    base = MD_MUL['fmulu  (Fox/Tqa, 4x512B LUT)  [10.11]']
    for name, cyc in sorted(MD_MUL.items(), key=lambda kv: kv[1]):
        per_edge = MULS_PER_EDGE * cyc
        tag = '  <== GAME USES THIS (the MD\'s fastest)' if cyc == base else \
              f'  = +{per_edge - MULS_PER_EDGE*base:>4} cyc/edge vs fmulu'
        print(f'   {cyc:>5} cyc  x2 = {per_edge:>5} cyc/edge   {name}{tag}')
    print(f'\n   (game\'s fmul_b body counted here = {FMUL_B_CORE} cyc incl. 2x sta qp->ZP;')
    print(f'    the MD\'s "14" is the register-only A:Y core -- same technique.)')

    print('\n DIVIDE AVOIDANCE (calc_step: slope = |dx| * recip[dy], recip=65536/dy):')
    RECIP_SIMPLE = MULS_PER_EDGE * FMUL_B_CORE
    print(f'   MD way  (reciprocal-LUT + 2x fmulu) : ~{RECIP_SIMPLE:>4} cyc (the 2 core muls)')
    print(f'   naive   (16-iter restoring divide)  : ~{DIV16_REF:>4.0f} cyc  (no MD constant-')
    print(f'           divisor shortcut helps: dy is a runtime variable)')
    print(f'   -> the MD divide-avoidance saves ~{DIV16_REF - RECIP_SIMPLE:.0f} cyc PER EDGE.'
          '  This is the')
    print('      "reciprocal LUT + QS multiply instead of a 32/16 divide" pattern the')
    print('      skill names -- already banked in calc_step (aw_polygon.asm:327).')


def report():
    print('=' * 70)
    print(' CYCLE-EXACT HOT PATH  (GAME, from real ASM ; PAL 6502 = 1.773 MHz)')
    print('=' * 70)
    print('\n ONE DRAWN SCANLINE  (full-detail ?row, fast clip, LR, colour cached):')
    for k, v in sorted(PER_SCANLINE.items(), key=lambda kv: -kv[1]):
        print(f'   {v:4d} cyc  {100*v/SCAN_TOTAL:4.1f}%  {bar(v, SCAN_TOTAL)}  {k}')
    print(f'   {SCAN_TOTAL:4d} cyc 100.0%  {"="*34}  TOTAL / scanline')
    math = PER_SCANLINE['add_steps (16.16 DDA)']
    vbxe = PER_SCANLINE['fill_span (BCB+fire)']
    print(f'\n   -> fixed-point MATH (add_steps)      : {math:4d} cyc = {100*math/SCAN_TOTAL:.0f}%')
    print(f'   -> VBXE/BCB setup+fire (fill_span)   : {vbxe:4d} cyc = {100*vbxe/SCAN_TOTAL:.0f}%'
          '  (mostly native-speed even on Rapidus)')
    print(f'   -> clip/order/emit (dsl+emit)        : {DSL_FAST+EMIT_LR:4d} cyc = '
          f'{100*(DSL_FAST+EMIT_LR)/SCAN_TOTAL:.0f}%')

    print(f'\n PER EDGE  calc_step (reciprocal-LUT + 2x fmulu, skill-optimal):')
    print(f'   {PER_EDGE_NODBL:4d} cyc  full-detail (poly_bcb_h=0, no doubling)')
    print(f'   {PER_EDGE_FULL:4d} cyc  half-detail (poly_bcb_h=1, + smc2 doubling pass)')
    print(f'   of which fmul_seta+2x fmul_b (the 3 multiplies) = '
          f'{FMUL_SETA + 2*(FMUL_B) + 3*JSR} cyc')

    print(f'\n PER COORD  read_scaled:')
    print(f'   {RS_FAST:4d} cyc  rs_fast (zoom==64, the common case: no multiply)')
    print(f'   {RS_Z4:4d} cyc  rs_z4  (zoom!=64: 2 table multiplies, premultiplied z4)')

    md_bakeoff()
    a16_report()

    print('\n' + '=' * 70)
    print(' CANDIDATES  (delta per scanline unless noted ; ranked by safe saving)')
    print('=' * 70)

    # C1 : SMC-dispatch the per-span `hires` test (frame-constant) like rs_smc/smc_dsl
    c1_emit = s(('lda', 'abs'), ('br', False))        # lda hires ; bne ?sr  (removed)
    c1_fill = s(('ldy', 'abs'), ('br', True))         # ldy hires ; beq ?lrlut (removed)
    c1 = c1_emit + c1_fill
    print(f'\n [C1] SMC-dispatch `hires` (LR/SR) once per FRAME, not per span')
    print(f'      emit_span drops "lda hires/bne" ({c1_emit}c) + fill_span drops '
          f'"ldy hires/beq" ({c1_fill}c)')
    print(f'      SAVE {c1:2d} cyc/scanline  ({100*c1/SCAN_TOTAL:.1f}% of the body)  '
          f'-- BIT-EXACT, same idiom as rs_smc/smc_dsl/smc_yj already in the loop.')

    # C2 : inline draw_scanline_fast into ?row (kill the jsr + the final rts)
    c2 = JSR + C[('rts', None)]
    print(f'\n [C2] inline draw_scanline_fast into ?row (SMC-select the body, no jsr)')
    print(f'      SAVE {c2:2d} cyc/scanline  ({100*c2/SCAN_TOTAL:.1f}%)  -- BIT-EXACT, but'
          ' costs code (3 inline variants or an SMC body-swap); medium effort.')

    # C3 : drop the fraction to 16.8 (24-bit accumulator: remove cr0/cl0 bytes)
    c3 = 2 * _add1_byte
    drift = frac8_drift()
    print(f'\n [C3] 16.16 -> 16.8 edge accumulator (drop cr0/cl0 fraction byte)')
    print(f'      SAVE {c3:2d} cyc/scanline  ({100*c3/SCAN_TOTAL:.1f}%)  in add_steps.')
    print(f'      *** NOT bit-exact.  Measured drift vs 16.16 over dy=2..199, all dx:')
    print(f'          max |x| divergence = {drift["max_px"]} px on {drift["bad_edges"]} of '
          f'{drift["edges"]} edges ; {drift["bad_rows"]} rows differ by >=1px.')
    print(f'      -> would need re-verify (verify_halfstep.py) + Altirra eyeball; the'
          ' "floating steps" risk you already rejected once.')

    print('\n' + '=' * 70)
    print(' VERDICT  (math.md-centric)')
    print('=' * 70)
    print(' * Of the WHOLE math.md menu, only two techniques map onto this engine and')
    print('   BOTH are already applied at their fastest setting:')
    print('     - 8x8 multiply -> fmulu (14c), the fastest option in the MD table')
    print('       (QS would be +30c/edge, mult_fast +62c/edge, smallest +3062c/edge).')
    print(f'     - divide -> reciprocal-LUT + 2x fmulu (~{RECIP_SIMPLE}c) instead of a real')
    print(f'       ~{DIV16_REF:.0f}c restoring divide: the MD\'s "reciprocal instead of divide".')
    print(' * The rest of the MD (sin/cos 10.6, atan2 10.9, sqrt 10.10/10.12, splines')
    print('   10.13, constant mul3/10/96, 65816) does NOT apply: Another World has no')
    print('   runtime rotation, distance, curve or fixed-scale math -- all baked into')
    print('   the polygon data upstream. So there is no unused MD trick left to gain.')
    print(f' * The remaining per-scanline cost is non-arithmetic: fill_span/VBXE ({vbxe}c)')
    print('   + clip/emit (110c). The only bit-exact win left is dispatch, not math')
    print(f'   ([C1] hires-SMC, {c1}c/scanline). The one "math" saving ([C3] 16.8) breaks')
    print('   bit-exactness on 73% of edges -- reject, per the numbers above.')


def frac8_drift():
    """Honest drift: does rounding the DDA slope to 1/256 (16.8) instead of 1/65536
    (16.16) ever change the INTEGER x drawn on a row? Signed big-int floor math, no
    16-bit masking (the earlier masked version reported a bogus 65535 wrap artifact
    on dx<0). Both start at the same integer x0=0; only the slope rounding differs.
    Matches the real code's semantics: drawn x = high word of the accumulator."""
    max_px = 0
    bad_edges = bad_rows = edges = 0
    for dy in range(2, 200):
        for dx in range(-319, 320):
            edges += 1
            step16 = round(dx / dy * 65536)
            step8 = round(dx / dy * 256)
            acc16 = acc8 = 0            # x0 = 0, both fractions 0
            edge_bad = False
            for _ in range(dy):
                acc16 += step16
                acc8 += step8
                d = abs((acc16 >> 16) - (acc8 >> 8))   # floor for signed via >>
                if d:
                    bad_rows += 1
                    edge_bad = True
                    max_px = max(max_px, d)
            if edge_bad:
                bad_edges += 1
    return dict(max_px=max_px, bad_edges=bad_edges, bad_rows=bad_rows, edges=edges)


if __name__ == '__main__':
    report()
