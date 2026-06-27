;==============================================================
; PoP/VBXE - VBXE register & constant reference
;   FX core v1.20+, base $D600. (Adapted from woll3d/src/vbxe_regs.inc.)
;==============================================================

; --- Atari hardware regs used by the harness ---
SDMCTL          equ $022F     ; OS shadow of DMACTL
RTCLOK3         equ $0014     ; jiffy frame counter
DMACTL          equ $D400
NMIEN           equ $D40E
PORTB           equ $D301     ; PIA port B: memory/bank control (XL/XE)

; --- VBXE registers (base $D600) ---
VBXE_BASE       equ $D600
VBXE_VCTL       equ VBXE_BASE+$40   ; w: VIDEO_CONTROL   r: CORE_VERSION
VBXE_XDLA0      equ VBXE_BASE+$41   ; w: XDL addr lo     r: MINOR_REVISION
VBXE_XDLA1      equ VBXE_BASE+$42   ; w: XDL addr mid
VBXE_XDLA2      equ VBXE_BASE+$43   ; w: XDL addr hi
VBXE_CSEL       equ VBXE_BASE+$44   ; w: color index (auto-inc on CB write)
VBXE_PSEL       equ VBXE_BASE+$45   ; w: palette index (0..3)
VBXE_CR         equ VBXE_BASE+$46   ; w: red
VBXE_CG         equ VBXE_BASE+$47   ; w: green
VBXE_CB         equ VBXE_BASE+$48   ; w: blue (commits, advances CSEL)
VBXE_BL_ADR0    equ VBXE_BASE+$50   ; w: blitter list addr lo
VBXE_BL_ADR1    equ VBXE_BASE+$51   ; w: blitter list addr mid
VBXE_BL_ADR2    equ VBXE_BASE+$52   ; w: blitter list addr hi
VBXE_BL_START   equ VBXE_BASE+$53   ; w: write 1 to start
VBXE_BL_BUSY    equ VBXE_BASE+$53   ; r: non-zero while running
VBXE_MEMAC_B    equ VBXE_BASE+$5D   ; legacy MEMAC-B
VBXE_MEMAC_CTL  equ VBXE_BASE+$5E   ; MEMAC-A control
VBXE_BANK_SEL   equ VBXE_BASE+$5F   ; MEMAC-A bank select

CORE_FX_1XX     equ $10        ; expected VBXE_VCTL (read) value
MINOR_MASK      equ $70
MINOR_V120      equ $20

; --- VBXE alternate base $D700 (PROBE ONLY) ---
; VBXE can be jumpered to $D600 (default) or $D700. The engine addresses all
; registers absolutely at $D600, so it can only RUN at $D600 -- these aliases
; let detect_vbxe still PROBE $D700 to report "wrong base" vs "no VBXE".
VBXE_BASE_ALT   equ $D700
VBXE_VCTL_ALT   equ VBXE_BASE_ALT+$40   ; r: CORE_VERSION at $D700
VBXE_XDLA0_ALT  equ VBXE_BASE_ALT+$41   ; r: MINOR_REVISION at $D700

; --- VIDEO_CONTROL bits ---
VC_XDL_ON       equ $01
VC_XCOLOR       equ $02
VC_NO_TRANS     equ $04        ; disable color-0 transparency

; --- MEMAC-A control byte ---
;   bits 0-1 window size 00=4K 01=8K 10=16K 11=32K
;   bit 2 MAE (ANTIC), bit 3 MCE (CPU), bits 4-7 window base hi nibble
MC_4K           equ %00
MC_8K           equ %01
MC_16K          equ %10
MC_32K          equ %11
MC_ANTIC        equ $04
MC_CPU          equ $08

; --- MEMAC-A bank select (bit7 = global enable, bits6-0 = 4K bank) ---
BANK_EN         equ $80

; --- XDL control bits, byte 1 ---
XDLC_TMON       equ $01
XDLC_GMON       equ $02
XDLC_OVOFF      equ $04
XDLC_MAPON      equ $08
XDLC_MAPOFF     equ $10
XDLC_RPTL       equ $20
XDLC_OVADR      equ $40
XDLC_OVSCRL     equ $80

; --- XDL control bits, byte 2 ---
XDLC_CHBASE     equ $01
XDLC_MAPADR     equ $02
XDLC_MAPPAR     equ $04
XDLC_ATT        equ $08
XDLC_HR         equ $10        ; high-res overlay (4bpp nibble)
XDLC_LR         equ $20        ; low-res overlay  (8bpp, 1 byte = 2 hw px)
                              ; (neither HR nor LR = SR: 8bpp, 1 byte = 1 hw px, 320 wide)
XDLC_END        equ $80

; --- XDL ATT byte 1 - overlay width + palette ---
OV_NARROW       equ $00        ; 256 hw px
OV_NORMAL       equ $01        ; 320 hw px
OV_WIDE         equ $02        ; 336 hw px
OV_PAL0         equ $00
OV_PAL1         equ $10
OV_PAL2         equ $20
OV_PAL3         equ $30

; --- XDL ATT byte 2 - priority bitmask ($FF = overlay over everything) ---
PRI_ALL         equ $FF

; --- Blitter modes (BCB byte 20, bits 0-2) ---
BLT_COPY        equ $00
BLT_BSTENCIL    equ $01        ; byte stencil: skip src==0 (our transparent index)
BLT_ADD         equ $02
BLT_OR          equ $03
BLT_AND         equ $04
BLT_XOR         equ $05
BLT_NSTENCIL    equ $06        ; nibble stencil (HR)
BLT_NEXT        equ $08        ; chain to next BCB

; --- BCB layout (21 bytes) ---
BCB_SIZE        equ 21
BCB_SRC_ADDR    equ 0
BCB_SRC_STEPY   equ 3
BCB_SRC_STEPX   equ 5
BCB_DST_ADDR    equ 6
BCB_DST_STEPY   equ 9
BCB_DST_STEPX   equ 11
BCB_WIDTH       equ 12
BCB_HEIGHT      equ 14
BCB_AND         equ 15
BCB_XOR         equ 16
BCB_COLLIDE     equ 17
BCB_ZOOM        equ 18
BCB_PATTERN     equ 19
BCB_CTRL        equ 20
