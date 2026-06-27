
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
N2 equ RAMB+18
N3 equ RAMB+19
V0 equ RAMB+20
V1 equ RAMB+21
R0 equ RAMB+22
R1 equ RAMB+23
dvsign equ RAMB+24
dv_lo  equ RAMB+25
dv_hi  equ RAMB+26
; raster loop state
hy_lo equ RAMB+27
hy_hi equ RAMB+28
i_idx equ RAMB+29
j_idx equ RAMB+30
numv  equ RAMB+31
hgt_lo equ RAMB+32                  ; segment height h (signed)
hgt_hi equ RAMB+33
hh     equ RAMB+34                  ; max(h,1) (divisor, 8-bit)
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
row_cnt equ RAMB+47
poly_color equ RAMB+48
; multiply
mul_m equ RAMB+49
prod0 equ RAMB+50
prod1 equ RAMB+51
prod2 equ RAMB+52
scaled_lo equ RAMB+53
scaled_hi equ RAMB+54
; decoder locals
bbw equ RAMB+55                     ; (2)
bbh equ RAMB+57                     ; (2)
x0  equ RAMB+59                     ; (2)
y0  equ RAMB+61                     ; (2)
bx  equ RAMB+63                     ; (2)
by  equ RAMB+65                     ; (2)
cx  equ RAMB+67                     ; (2)
cy  equ RAMB+69                     ; (2)
word_lo equ RAMB+71
word_hi equ RAMB+72
ccol  equ RAMB+73
hcount equ RAMB+74
nverts equ RAMB+75
fill_col equ RAMB+76
vidx  equ RAMB+77
; half-res render gate (detect_cpu): 0 = full detail (Rapidus/65C816 -- draw every
; scanline, 1-tall spans), 1 = half vertical res (stock 6502 -- draw even hy only,
; 2-tall spans). ONE byte does both jobs: it is the poly span BCB HEIGHT field
; (height-1 -> 0=1 row, 1=2 rows) AND the ?row parity mask (and hy_lo -> 0=all, 1=even).
; NOTE: +93..+101 of this gap are taken by the GAME's vm_* scratch (game_vm.asm), so
; this MUST live at +102 or above (a collision at +93 made BCB_HEIGHT = the VM dispatch
; vector -> tall spans -> vertical streaks). +102..+127 is free in both builds.
poly_bcb_h equ RAMB+102
; op_drawtext (0x07) scratch  (free RAMB gap +78..+127, below the vertex buffers)
txt_x   equ RAMB+78
txt_y   equ RAMB+79
txt_col equ RAMB+80
t_sidlo equ RAMB+81                ; strId lo (pl_byte clobbers tmp_lo, so don't use it)
t_sidhi equ RAMB+82                ; strId hi
t_cx    equ RAMB+83                ; current char column
t_ch    equ RAMB+84               ; current char byte
t_cbx   equ RAMB+87               ; (2) cx*8 (320-space base column)
t_rbits equ RAMB+89               ; current font row bits (shifted MSB-first)
t_inrun equ RAMB+90              ; 1 = inside a run of set pixels
t_i0    equ RAMB+91              ; run start bit index
t_j     equ RAMB+92              ; glyph row 0..7
; vertex buffers (64 verts) and recursion stack
pts_xlo equ RAMB+128               ; +128..+191
pts_xhi equ RAMB+192               ; +192..+255
pts_ylo equ RAMB+256
pts_yhi equ RAMB+320
pstk    equ RAMB+384               ; software recursion stack (256 B)
