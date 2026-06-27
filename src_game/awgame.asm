;=============================================================================
; awgame.asm  -  "Another World" FULL GAME port for Atari XE/XL + VBXE
;
;   SEPARATE build from the intro. The intro (src/awvbxe.asm + its modules) is
;   left untouched. This build REUSES the shared engine modules read-only via
;   icl (polygon decoder, 16.16 raster, VBXE/blitter, equates) and replaces the
;   intro's playlist replayer with the real Another World VM (Phase 1+).
;
;   --- PHASE 0 (current) : scaffolding ---
;       boot + VBXE bring-up + clear pages + render ONE polygon from streamed
;       VRAM, to prove the shared engine lives in the new source tree. The real
;       VM, resource loading, input and sound come in later phases.
;
;   Build:  mads.exe src_game/awgame.asm -o:awgame.xex   (run from the project root)
;=============================================================================

        icl 'src/vbxe.inc'             ; VBXE_*, BCB_*, XDLC_*, OV_*, BLT_*, MC_* ...

COLBK       equ $D01A                  ; GTIA background/border colour
COLOR4      equ $02C8                  ; OS shadow of COLBK
PAL         equ $D014                  ; GTIA PAL/NTSC flag (bits 1-3): NTSC=$0F, PAL=$01 (set = NTSC)

; OS CIO + RAMTOP, for vbxe_err's no-VBXE message (same E: editor + CIO method as
; the intro's src/aw_exit.asm). RAMTOP is bumped down first: with BASIC off at boot
; the OS editor screen lands at $BC40, inside the game's $B400 segment, so it must
; be rebuilt in free RAM (the intro tops out at $B000, so its screen is never hit).
RAMTOP      equ $6A                    ; OS top-of-RAM page; editor screen placed below it
CIOV        equ $E456                  ; OS CIO entry
ICCOM       equ $0342                  ; IOCB #0: command
ICBAL       equ $0344                  ;          buffer addr lo / hi
ICBAH       equ $0345
ICBLL       equ $0348                  ;          buffer length lo / hi
ICBLH       equ $0349
ICAX1       equ $034A                  ;          aux 1 / aux 2
ICAX2       equ $034B

        icl 'src/aw_equates.inc'       ; resolution switch, VRAM map, zero page, work RAM
        icl 'src_game/game_zp.inc'     ; GAME-only: decoder locals -> ZP (aw4.txt, union on $C0-$C6)

; --- runtime HI-RES (SR 320) switch, GAME build only (defined BEFORE the engine icl so
; the shared raster/vbxe modules compile their runtime-switch path; the INTRO never
; defines HIRES_CAP -> .ifdef skips it -> intro stays compile-time LR, byte-identical).
; hires = 0 : LR 160 (every gameplay scene) ; 1 : SR 320 (the access-code part 16008 only,
; so the password letters are readable -- zad.txt). Toggled in load_part by part index. ---
HIRES_CAP = 1
hires = RAMB+103                    ; +103..+127 free in both builds (see aw_equates note)
cpu_detail = RAMB+104              ; saved detect_cpu value (0 Rapidus full / 1 stock half-vert);
                                   ; restored on LR parts, overridden to 0 (full) for 16008
rpar      = RAMB+105              ; half-res per-polygon row parity (relative to the poly top, so
                                  ; small polygons aren't dropped in half mode -- jail textures)
HR_SR_W   = 320                     ; SR overlay width (bytes/line) and page stride
SR_PXMODE = XDLC_ATT | XDLC_END     ; SR overlay control byte (no XDLC_LR)
LR_PXMODE = XDLC_ATT | XDLC_END | XDLC_LR

;=============================================================================
        org $2000

game_start
        sei
        lda PORTB
        ora #$02                       ; disable BASIC, keep OS ROM
        sta PORTB
        lda #0
        sta SDMCTL                     ; ANTIC playfield DMA off (VBXE overlay covers all)
        sta DMACTL
        sta COLOR4                     ; black border
        sta COLBK
        cli

        jsr detect_vbxe
        bcc ?ok                        ; C=0 -> VBXE at $D600, proceed
        jmp vbxe_err                   ; C=1 -> A = $01 ($D700) / $00 (none)
?ok
        jsr detect_cpu                 ; Rapidus(65C816) -> full ; stock 6502 -> half-res
        lda poly_bcb_h                  ; remember the detected detail level (0 full / 1 half)
        sta cpu_detail                  ;   so LR parts restore it; 16008 forces full (=Rapidus)
        lda PAL                        ; read $D014 (NTSC=$0F, PAL=$01); corrected by eor below
        and #$0E
        eor #$0E                        ; FIX: $D014 reads NTSC=$0F, PAL=$01, so &$0E gives
                                        ;   $0E on NTSC / $00 on PAL -- INVERTED. eor flips it so
                                        ;   is_pal != 0 truly means PAL (pace skips comp on PAL).
        sta is_pal
        jsr setup_memac
        jsr setup_xdls
        jsr upload_bcb
        jsr pal_init_black

        lda #<BCBF_V                   ; blitter list address (constant) loaded once
        sta VBXE_BL_ADR0
        lda #>BCBF_V
        sta VBXE_BL_ADR1
        lda #[BCBF_V>>16]
        sta VBXE_BL_ADR2

        lda #0                         ; start in LR (gameplay); 16008 flips to SR
        sta hires
        ldx #0                         ; clear all 4 video pages to black
        lda #0
        jsr clear_page
        ldx #0
        lda #1
        jsr clear_page
        ldx #0
        lda #2
        jsr clear_page
        ldx #0
        lda #3
        jsr clear_page

        lda #VC_XDL_ON | VC_XCOLOR | VC_NO_TRANS
        sta VBXE_VCTL

        lda #0
        sta cur_draw
        jsr set_cbase_cur

        lda #0                         ; MEMAC-B bank cache + span cache init
        sta memb_cur
        sta poly_hi
        lda #$80+POLY_BANK0
        sta poly_bnk
        lda #>DATAW
        sta pb_ptr+1
        lda #$FF
        sta last_scol

;-----------------------------------------------------------------------------
; PHASE 1 : run the Another World VM (water part, 16002) frame by frame. The VM
; drives the page/palette/draw engine itself; it halts on a part switch (op_memlist)
; until Phase 2 adds resource loading.
;-----------------------------------------------------------------------------
        jsr snd_init                   ; POKEY SFX player : hook Timer 1 IRQ (loading done)
        jsr vm_init
?frame  jsr vm_run_frame
        lda vm_running
        bne ?frame
?halt   jmp ?halt                      ; VM halted (part switch / no active threads)

; VBXE missing or at the wrong base: show the no-VBXE message the standard Atari
; way, exactly like the intro (src/aw_exit.asm) -- a normal GR.0 editor screen +
; CIO text. The only addition: the game's $B400 segment overwrote the OS editor
; screen ($BC40 when BASIC is off at boot), so first move RAMTOP into free RAM and
; CLOSE/re-OPEN E: to rebuild a clean screen there. Then print and halt.
; .proc keeps the ? local out of the other modules' label scope.
.proc vbxe_err
        lda #$A0
        sta RAMTOP                     ; editor screen -> $9C40 (free RAM above pal_data)
        lda #0
        sta COLOR4                     ; black border
        sta COLBK
        lda #$22
        sta SDMCTL                     ; ANTIC playfield DMA back on
        cli                            ; CIO / editor need IRQs
        ldx #0                         ; CLOSE #0 : release the boot editor IOCB so OPEN rebuilds
        lda #$0C
        sta ICCOM
        jsr CIOV
        ldx #0                         ; OPEN #0 "E:" : fresh GR.0 editor screen at RAMTOP
        lda #3
        sta ICCOM
        lda #<nv_edev
        sta ICBAL
        lda #>nv_edev
        sta ICBAH
        lda #$0C
        sta ICAX1
        lda #0
        sta ICAX2
        jsr CIOV
        ldx #0                         ; PUT RECORD : print the message
        lda #$09
        sta ICCOM
        lda #<nv_msg
        sta ICBAL
        lda #>nv_msg
        sta ICBAH
        lda #<nv_len
        sta ICBLL
        lda #>nv_len
        sta ICBLH
        jsr CIOV
?halt   jmp ?halt
nv_edev dta c'E:',$9B
nv_msg  dta c'NEEDS VBXE',$9B
nv_len  equ *-nv_msg
.endp

;=============================================================================
; Shared engine modules (read-only reuse from the intro tree -- DO NOT edit
; these for the game; copy into src_game/ if a game-specific change is needed).
;=============================================================================
        icl 'src_game/aw_polygon.asm'  ; poly decoder (GAME fork: video2 base select)
        icl 'src/aw_raster.asm'        ; integer 16.16 raster, scanline -> span
        icl 'src/aw_vbxe.asm'          ; VBXE bring-up, palette, page/blitter, recip/row
        icl 'src_game/game_vm.asm'     ; the Another World bytecode VM (Phase 1)

; disable_basic : runs DURING LOAD (as an `ini` segment -- the bootloader's do_init JSRs
; it, and the OS XEX loader runs it too) so $A000-$BFFF (RAM under the BASIC ROM on XL) is
; writable when the relocated modules below load there. Without this the $B400 segments are
; written to ROM = ignored = garbage code = black screen / KIL.
disable_basic
        lda PORTB
        ora #$02                       ; bit1 = 1 : BASIC ROM off -> RAM at $A000-$BFFF
        sta PORTB
        rts
        ini disable_basic

; --- modules RELOCATED out of the $2000-$3FFF code segment ---------------------------
; The $2000 code segment had overflowed past $4000 into the MEMAC-B data window
; ($4000-$7FFF): running that code executed VRAM bytes (KIL). game_diskio + game_sound
; load at $B400-$BFFF (free RAM above the VM state which ends at $B3D0; BASIC disabled by
; the ini above). Placed BEFORE game_data.asm's `run` segment so the loader loads them
; (the loader JMPs at RUN, so later segments would never load).
        org $B400
        icl 'src_game/game_diskio.asm' ; runtime ATR part loader (Phase 2)
        icl 'src_game/game_sound.asm'  ; POKEY SFX player (op_sound) -- per-part VRAM samples

; game_text RELOCATED to $0900 (its own segment): the $B400-$BFFF block filled up (ceiling
; is $C000 = OS ROM), so DRAWTEXT could not grow there. $0900-$0FFF is free at RUN time --
; the boot loader's working area is $0700-$087F (dead once it JMPs to the game), our other
; segments all start >= $1000, and game_diskio streams sectors straight into the VRAM window
; ($4000), not low RAM. ~1.75 KB of headroom here for the glyph blitter. (Guarded by
; tools/check_xex.py: $0900 is not a reserved range and doesn't overlap any other segment.)
        org $0900
        icl 'src_game/game_text.asm'   ; op_drawstring (DRAWTEXT) -- intro glyph blitter

; Text data (font + 139-string table) at `org $1000` (free low RAM, clear of the VM
; vars/threads at $B000+), BEFORE game_data.asm's `run` segment.
        org $1000
        icl 'src_game/game_text_data.inc'  ; aw_font + aw_id_*/aw_str_*/aw_strbytes (139 strings)

; VRAM shape-cell cache (stage 1) -- decode+raster of recurring draws replaced by
; cell blits; lives in the free $AA00-$AFFF gap (sets its own org).
        icl 'src_game/game_cellcache.asm'
;=============================================================================
        icl 'src_game/game_data.asm'   ; pal_data, fmulu/poly tables, VRAM streaming, run
