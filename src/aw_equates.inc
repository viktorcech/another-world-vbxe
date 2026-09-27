
;-----------------------------------------------------------------------------
; Resolution switch  (LR 160x200 is the on-hardware target)
;-----------------------------------------------------------------------------
LORES       equ 1
DIAG        equ 0                   ; 1 = isolation test (draw a single shape outside the playlist)

.if LORES
SCRW        equ 160
XDL_PXMODE  equ XDLC_LR
ROWBIAS     equ $4000               ; X bias ($8000) survives a>>1 -> +$4000 in sx (LR)
.else
SCRW        equ 320
XDL_PXMODE  equ 0
ROWBIAS     equ $8000               ; SR: no >>1, the full $8000 bias survives in sx
.endif
SCRH        equ 200
WMAX        equ 319                 ; clip limit in 320-space (raster math is 320-wide)

;-----------------------------------------------------------------------------
; VRAM map (see docs/PORT.md).  Pages on 64 KB boundaries; data 16 KB-aligned.
;   $000000..$030000  Page 0..3   (LR 160x200 = 32000 B each)
;   $040000           control bank (XDL0..3 + BCB)   -> MEMAC-A $8000 window
;   $050000           poly data    (banks $14..$17)  -> MEMAC-B $4000 window
;-----------------------------------------------------------------------------
CTRL        equ $040000
CTRL_BANK   equ CTRL/$1000          ; $40
POLY_BASE   equ $050000
POLY_BANK0  equ POLY_BASE/$4000     ; $14 : first MEMAC-B bank of the poly data
PLAY_BASE   equ $060000
PLAY_BANK0  equ PLAY_BASE/$4000     ; $18 : first MEMAC-B bank of the playlist

MEMW        equ $8000               ; MEMAC-A CPU window (4K) into the control bank
MEMW_HI     equ >MEMW
DATAW       equ $4000               ; MEMAC-B CPU window (16K) into data

XDL0_OFF    equ $000                ; XDL0..XDL3 at $000,$040,$080,$0C0
BCBF_OFF    equ $100                ; fill / span / copy BCB
BCB         equ MEMW+BCBF_OFF       ; CPU-side address of the BCB (in the window)
BCBF_V      equ CTRL+BCBF_OFF       ; $040100 : absolute VRAM addr (for BL_ADR)
; vbxe-blitter skill: pre-filled BCB templates, one per slot, so a caller writes only
; the fields that change and never clobbers the span BCB (no mode re-patch after it).
; All live in the $0401xx page: a START picks its list by BL_ADR0 alone.
BCBP_OFF    equ $115                ; page clear (AND 0 / XOR colour)
BCBK_OFF    equ $12A                ; page copy
BCBC_OFF    equ $13F                ; GAME cell blit: 2 lists x 2 chained BCBs ($13F-$192)
BCBP        equ MEMW+BCBP_OFF
BCBK        equ MEMW+BCBK_OFF
BCBC        equ MEMW+BCBC_OFF
        ert >[BCBC_OFF+4*21-1]<>>BCBF_OFF   ; every list in BL_ADR0's page

;-----------------------------------------------------------------------------
; Zero page  ($80-$D3 free, BASIC off)
;-----------------------------------------------------------------------------
pl_lo       equ $80                 ; (2) free (was the 24-bit playlist address;
pl_mid      equ $81                 ;     pl_byte now runs on pl_wlo/pl_whi+pl_bnk)
cbase       equ $82                 ; (3) base of the current draw page
cur_draw    equ $85                 ; current draw page index
dr_off      equ $86                 ; (2) poly read offset (draw() local)
poff        equ $88                 ; (2) poly_byte argument
pb_ptr      equ $8A                 ; (2) poly_byte window pointer
scol        equ $8C                 ; fill_span colour / mode
sx_lo       equ $8D
sx_hi       equ $8E
sy          equ $8F
slen_lo     equ $90
slen_hi     equ $91
zp_dlo      equ $92                 ; fill_span scratch (offset)
zp_dmid     equ $93
tmp_lo      equ $94
tmp_hi      equ $95
dr_x        equ $96                 ; (2) draw() x   (signed)
dr_y        equ $98                 ; (2) draw() y
dr_zoom     equ $9A                 ; (2) draw() zoom
dr_col      equ $9C                 ; draw() colour
psp         equ $9D                 ; recursion software-stack pointer
cp_src      equ $9E
cp_dst      equ $9F
blit_pg     equ $A0
hold        equ $A1
pal_ptr     equ $A2                 ; (2) set_palette / dptr scratch
dptr        equ $A4                 ; (2) setup_xdls scratch
acc_lo      equ $A6                 ; set_palette N*48 scratch
acc_hi      equ $A7
t_lo        equ $A8
t_hi        equ $A9
zp_n        equ $AA
pl_bnk      equ $AB                 ; pl_byte's current MEMAC-B bank ($80|bank)
; --- MEMAC-B bank cache (#2): avoid re-switching the bank on every byte ---
memb_cur    equ $AC                 ; mirror of the current VBXE_MEMAC_B value
poly_hi     equ $AD                 ; free (was the poly_byte cache high byte)
poly_bnk    equ $AE                 ; cached poly bank value (= $80|bank)
pl_wlo      equ $AF                 ; (2) pl_byte's own window pointer
pl_whi      equ $B0
last_scol   equ $B1                 ; cached span colour/mode (skip BCB mode re-patch)
pend_pal    equ $B2                 ; deferred palette index ($FF = none); applied at BLIT
txt_ptr     equ $B3                 ; (2) op_drawtext char ptr into aw_strbytes (needs (zp),y)
t_fp        equ $B5                 ; (2) op_drawtext glyph ptr into aw_font   (needs (zp),y)
is_pal      equ $B7                 ; 0 = NTSC (60 Hz), non-zero = PAL (50 Hz)
; fmulu (8x8->16 square-table multiply) product
qp_lo       equ $BE                 ; product low
qp_hi       equ $BF                 ; product high

;-----------------------------------------------------------------------------
; Work RAM (above the MEMAC-A window; never inside $4000-$7FFF or $8000-$8FFF)
;-----------------------------------------------------------------------------
RAMB        equ $9C00
; 16.16 edge accumulators + steps : the per-scanline add_steps hot loop hammers
; these, so they live in ZERO PAGE ($C0-$CF) -- ZP access is 3 cyc vs 4 for the
; absolute RAMB form (~3.2k cyc/frame measured across the intro).
cr0 equ $C0
cr1 equ $C1
cr2 equ $C2
cr3 equ $C3
cl0 equ $C4
cl1 equ $C5
cl2 equ $C6
cl3 equ $C7
.if 1
; $C8-$CF : the old str0..stl3 (step_r/step_l) cells -- unused since the steps became
; SMC immediates in the ?row loop. Skill pass 2026-09-09: the HOTTEST raster/edge
; cells move here from the RAMB block ("move the code from the slow region to the fast
; one" -- every lda/sta/inc/dec on them costs 1 cycle less in zero page; game_zp.inc
; measured the same move for the decoder locals). Both builds share this file.
hy_lo   equ $C8                    ; current scanline y (per-row inc + tests)
hy_hi   equ $C9
row_cnt equ $CA                    ; rows left in the segment (per-row dec)
hh      equ $CB                    ; max(h,1) (divisor, 8-bit) -- calc_step, 3x per edge
dv_lo   equ $CC                    ; signed dx for calc_step, 3-4x per edge
dv_hi   equ $CD
dvsign  equ $CE                    ; calc_step sign flag
N1      equ $CF                    ; slope byte 1 (the most-touched N cell: 4x per edge)
; divide work area (N = dividend/quotient, V = divisor, R = remainder)
N0 equ RAMB+16
; RAMB+17 (N1) -> zero page, see $C8-$CF above
.else
str0 equ $C8                       ; step_r (4)
str1 equ $C9
str2 equ $CA
str3 equ $CB
stl0 equ $CC                       ; step_l (4)
stl1 equ $CD
stl2 equ $CE
stl3 equ $CF
; divide work area (N = dividend/quotient, V = divisor, R = remainder)
N0 equ RAMB+16
N1 equ RAMB+17
.endif
N2 equ RAMB+18
N3 equ RAMB+19
V0 equ RAMB+20
V1 equ RAMB+21
R0 equ RAMB+22
R1 equ RAMB+23
.if 1
; RAMB+24..26 (dvsign, dv_lo, dv_hi) -> zero page, see $C8-$CF above
; raster loop state
; RAMB+27..28 (hy_lo, hy_hi) -> zero page, see $C8-$CF above
.else
dvsign equ RAMB+24
dv_lo  equ RAMB+25
dv_hi  equ RAMB+26
; raster loop state
hy_lo equ RAMB+27
hy_hi equ RAMB+28
.endif
.ifdef GAME_ZP
i_idx equ $93
.else
i_idx equ RAMB+29
.endif
.ifdef INTRO_ZP
j_idx equ $94                    ; (the intro uses $A0)
.elseif .def GAME_ZP
j_idx equ $A0
.else
j_idx equ RAMB+30
.endif
.ifdef GAME_ZP
numv equ $92
.else
numv  equ RAMB+31
.endif
.ifdef INTRO_ZP
hgt_lo equ $D4                    ; (the intro uses $A1)
.elseif .def GAME_ZP
hgt_lo equ $A1
.else
hgt_lo equ RAMB+32                  ; segment height h (signed)
.endif
.ifdef GAME_ZP
hgt_hi equ $A6
.else
hgt_hi equ RAMB+33
.endif
.if 1
; RAMB+34 (hh) -> zero page, see $C8-$CF above
.else
hh     equ RAMB+34                  ; max(h,1) (divisor, 8-bit)
.endif
dvr_lo equ RAMB+35
dvr_hi equ RAMB+36
dvl_lo equ RAMB+37
dvl_hi equ RAMB+38
; a/b : the span endpoints, hot in draw_scanline (clip) + emit_span (the LR >>1) ->
; ZP (the $D0-$D3 slots freed by dropping xr/xl: draw_scanline now reads cr/cl direct).
a_lo  equ $D0
a_hi  equ $D1
b_lo  equ $D2
b_hi  equ $D3
.if 1
; RAMB+47 (row_cnt) -> zero page, see $C8-$CF above
.else
row_cnt equ RAMB+47
.endif
.ifdef GAME_ZP
poly_color equ $AA
.else
poly_color equ RAMB+48
.endif
; multiply
mul_m equ RAMB+49
prod0 equ RAMB+50
prod1 equ RAMB+51
prod2 equ RAMB+52
scaled_lo equ RAMB+53
scaled_hi equ RAMB+54
; decoder locals
.ifdef GAME_ZP
bbw equ $D5
.else
bbw equ RAMB+55                     ; (2)
.endif
.ifdef GAME_ZP
bbh equ $D7
.else
bbh equ RAMB+57                     ; (2)
.endif
x0  equ RAMB+59                     ; (2)
y0  equ RAMB+61                     ; (2)
.ifdef GAME_ZP
bx equ $D9
.else
bx  equ RAMB+63                     ; (2)
.endif
.ifdef GAME_ZP
by equ $DB
.else
by  equ RAMB+65                     ; (2)
.endif
cx  equ RAMB+67                     ; (2)
cy  equ RAMB+69                     ; (2)
.ifdef GAME_ZP
word_lo equ $DD
.else
word_lo equ RAMB+71
.endif
.ifdef GAME_ZP
word_hi equ $DE
.else
word_hi equ RAMB+72
.endif
.ifdef GAME_ZP
ccol equ $DF
.else
ccol  equ RAMB+73
.endif
.ifdef GAME_ZP
hcount equ $E0
.else
hcount equ RAMB+74
.endif
.ifdef GAME_ZP
nverts equ $A7
.else
nverts equ RAMB+75
.endif
.ifdef GAME_ZP
fill_col equ $A9
.else
fill_col equ RAMB+76
.endif
vidx  equ RAMB+77
; half-res render gate (detect_cpu): 0 = full detail (Rapidus/65C816 -- draw every
; scanline, 1-tall spans), 1 = half vertical res (stock 6502 -- draw even hy only,
; 2-tall spans). ONE byte does both jobs: it is the poly span BCB HEIGHT field
; (height-1 -> 0=1 row, 1=2 rows) AND the ?row parity mask (and hy_lo -> 0=all, 1=even).
; NOTE: +93..+101 of this gap are taken by the GAME's vm_* scratch (game_vm.asm), so
; this MUST live at +102 or above (a collision at +93 made BCB_HEIGHT = the VM dispatch
; vector -> tall spans -> vertical streaks). +102..+127 is free in both builds.
.ifdef INTRO_ZP
poly_bcb_h equ $E1                    ; (the intro uses $B2)
.elseif .def GAME_ZP
poly_bcb_h equ $B2
.else
poly_bcb_h equ RAMB+102
.endif
; op_drawtext (0x07) scratch  (free RAMB gap +78..+127, below the vertex buffers)
txt_x   equ RAMB+78
txt_y   equ RAMB+79
txt_col equ RAMB+80
t_sidlo equ RAMB+81                ; strId lo (pl_byte clobbers tmp_lo, so don't use it)
t_sidhi equ RAMB+82                ; strId hi
t_cx    equ RAMB+83                ; current char column
t_ch    equ RAMB+84               ; current char byte
.ifdef GAME_ZP
t_cbx equ $F7
.else
t_cbx   equ RAMB+87               ; (2) cx*8 (320-space base column)
.endif
.ifdef GAME_ZP
t_rbits equ $F9
.else
t_rbits equ RAMB+89               ; current font row bits (shifted MSB-first)
.endif
.ifdef GAME_ZP
t_inrun equ $FA
.else
t_inrun equ RAMB+90              ; 1 = inside a run of set pixels
.endif
.ifdef GAME_ZP
t_i0 equ $FB
.else
t_i0    equ RAMB+91              ; run start bit index
.endif
.ifdef GAME_ZP
t_j equ $FC
.else
t_j     equ RAMB+92              ; glyph row 0..7
.endif
; the decoder / raster scratch that the shared polygon + raster files use: plain
; aliases of the edge accumulators (dead during the vertex phase) and of poff.
g_scaled_lo equ cr0                 ; $C0   (mul_zoom output lo)
g_scaled_hi equ cr1                 ; $C1   (mul_zoom output hi)
g_x0        equ cr2                 ; $C2-$C3 (do_fill bbox origin x)
g_y0        equ cl0                 ; $C4-$C5 (do_fill bbox origin y)
g_vidx      equ cl2                 ; $C6   (do_fill vertex index)
; two calc_step slope cells at $88-$89 (poff: the old poly_byte argument, which
; neither build uses any more -- both decoders run on pb_ptr)
g_N0        equ $88                 ; slope byte 0
g_N2        equ $89                 ; slope byte 2
; vertex buffers (64 verts) and recursion stack
pts_xlo equ RAMB+128               ; +128..+191
pts_xhi equ RAMB+192               ; +192..+255
; The segment loop reads pts_y[i-1] / pts_y[j+1] as `pts_*-1,x` / `pts_*+1,x`, so each
; 64-entry array must sit at least 1 byte INSIDE its page and end at least 1 byte before
; it -- otherwise every such read crosses a page (+1 cycle each, every segment).
pts_ylo equ RAMB+257               ; $9D01..$9D40  (-1 and +63 stay in page $9D)
pts_yhi equ RAMB+322               ; $9D42..$9D81
pstk    equ RAMB+386               ; software recursion stack (256 B)

; nocross2 base, alo, ahi, blo, bhi : like nocross, for a routine with TWO stretches
;   that must each stay inside one page (6502 skill: a taken branch across a page is
;   +1). Tries no pad, then the pad that starts A on a page, then the one that starts
;   B on a page, and takes the first that keeps BOTH inside their pages.
.macro nocross2
?n2o = *
?n2a = [?n2o+[:2]-[:1]]&$FF
?n2al = [:3]-[:2]
?n2b = [?n2o+[:4]-[:1]]&$FF
?n2bl = [:5]-[:4]
?n2pa = [$100-?n2a]&$FF
?n2pb = [$100-?n2b]&$FF
.if [?n2a+?n2al<=$FF] && [?n2b+?n2bl<=$FF]
.elseif [[[?n2a+?n2pa]&$FF]+?n2al<=$FF] && [[[?n2b+?n2pa]&$FF]+?n2bl<=$FF]
:?n2pa dta 0
.elseif [[[?n2a+?n2pb]&$FF]+?n2al<=$FF] && [[[?n2b+?n2pb]&$FF]+?n2bl<=$FF]
:?n2pb dta 0
.else
        ert 1                   ; no single pad keeps both stretches in one page
.endif
.endm

; nocross base, lo, hi : zero bytes HERE (dead space, right before the routine at
;   `base`) so that lo..hi -- a hot taken branch's next PC .. its target, both inside
;   that routine -- sit in one page (a taken branch across a page costs 4, not 3).
;   The offsets are shift-invariant, so MADS recomputes the pad on every build.
; nocross3 base, a0,a1, b0,b1, c0,c1 : like nocross2, three stretches -- the smallest pad
;   (searched with .rept) that keeps each one inside a single page.
.macro nocross3
?n3o = *
?n3a = [:2]-[:1]
?n3al = [:3]-[:2]
?n3b = [:4]-[:1]
?n3bl = [:5]-[:4]
?n3c = [:6]-[:1]
?n3cl = [:7]-[:6]
?n3p = 256
.rept 256
.if ?n3p=256
.if [[[?n3o+#+?n3a]&$FF]+?n3al<=$FF] && [[[?n3o+#+?n3b]&$FF]+?n3bl<=$FF] && [[[?n3o+#+?n3c]&$FF]+?n3cl<=$FF]
?n3p = #
.endif
.endif
.endr
        ert ?n3p=256                ; no single pad keeps all three stretches in one page
.if ?n3p>0
:?n3p dta 0
.endif
.endm

; nocross4 base, a0,a1, b0,b1, c0,c1, d0,d1 : four stretches, one searched pad.
.macro nocross4
?n4o = *
?n4a = [:2]-[:1]
?n4al = [:3]-[:2]
?n4b = [:4]-[:1]
?n4bl = [:5]-[:4]
?n4c = [:6]-[:1]
?n4cl = [:7]-[:6]
?n4d = [:8]-[:1]
?n4dl = [:9]-[:8]
?n4p = 256
.rept 256
.if ?n4p=256
.if [[[?n4o+#+?n4a]&$FF]+?n4al<=$FF] && [[[?n4o+#+?n4b]&$FF]+?n4bl<=$FF] && [[[?n4o+#+?n4c]&$FF]+?n4cl<=$FF] && [[[?n4o+#+?n4d]&$FF]+?n4dl<=$FF]
?n4p = #
.endif
.endif
.endr
        ert ?n4p=256                ; no single pad keeps all four stretches in one page
.if ?n4p>0
:?n4p dta 0
.endif
.endm

.macro nocross
.if [[*+[:2]-[:1]]&$FF]+[:3]-[:2]>$FF
:[$100-[[*+[:2]-[:1]]&$FF]] dta 0
.endif
.endm
