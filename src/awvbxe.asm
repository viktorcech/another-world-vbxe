;=============================================================================
; awvbxe.asm  -  "Another World" intro remake for Atari XE/XL + VBXE
;
;   Atari XE/XL + VBXE FX core v1.2x, MADS assembler.   See docs/PORT.md.
;
;   PLAYLIST REPLAYER + POLYGON ENGINE.  The PC pipeline flattened the Another
;   World VM into a linear command stream (tools/aw_playlist.py); the 6502
;   replays it through the VBXE blitter. The full poly data and playlist are
;   streamed into VRAM at load time (INI segments) and read back through the
;   MEMAC-B window; the framebuffer is 4 logical pages in VRAM (LR 160x200).
;
;   This top file is just the spine: the global header, the VBXE/OS equates,
;   and the module include chain (split out of the original monolith; the icl
;   order reproduces the byte layout exactly). Module map:
;
;     src/aw_equates.inc   resolution switch, VRAM map, zero page, work RAM
;     src/aw_replayer.asm  start/init, the playlist dispatch + opcode handlers
;     src/aw_text.asm      op_drawtext (0x07) + the 8x8 glyph blitter
;     src/aw_exit.asm      no-VBXE message, intro_done (return to DOS)
;     src/aw_polygon.asm   far-data fetch, zoom/slope math, the poly decoder
;     src/aw_raster.asm    integer 16.16 raster, scanline -> span emit
;     src/aw_vbxe.asm      VBXE bring-up, palettes, page/blitter primitives
;     src/aw_data.asm      pal_data + font/strings + poly/playlist VRAM stream
;
;   CODE MUST STAY BELOW $4000 (the MEMAC-B window hides $4000-$7FFF when poly
;   reads are active); the big data tables live at $9000+ (above MEMAC-A).
;
;   Build:  mads.exe src\awvbxe.asm -o:awintro.xex    (run from the project root)
;=============================================================================

        icl 'src/vbxe.inc'         ; VBXE_*, BCB_*, XDLC_*, OV_*, BLT_*, MC_* ...

COLBK       equ $D01A              ; GTIA background/border colour (the overscan area)
COLOR4      equ $02C8              ; OS shadow of COLBK (VBI copies it every frame)
DOSVEC      equ $000A              ; OS vector to the resident DOS (return-to-DOS)
PAL         equ $D014              ; GTIA PAL/NTSC flag (bits 1-3): NTSC=$0F, PAL=$01 (set = NTSC)
CIOV        equ $E456              ; OS CIO entry (for the "no VBXE" message)
ICCOM       equ $0342              ; IOCB #0 : command / buffer / length fields
ICBAL       equ $0344
ICBAH       equ $0345
ICBLL       equ $0348
ICBLH       equ $0349
ICAX1       equ $034A
ICAX2       equ $034B

;=============================================================================
; Module includes (split out of the original monolith; same order = same code).
; equates first (symbols used everywhere), then code at $2000, then the data.
;=============================================================================
        icl 'src/aw_equates.inc'
        icl 'src/aw_replayer.asm'
        icl 'src/aw_text.asm'
        icl 'src/aw_exit.asm'
        icl 'src/aw_polygon.asm'
        icl 'src/aw_raster.asm'
        icl 'src/aw_vbxe.asm'
        icl 'src/aw_sound.asm'         ; POKEY SFX player (op 0x08) + VRAM sfx tables
        icl 'src/aw_data.asm'
