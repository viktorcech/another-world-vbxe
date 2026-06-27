;=============================================================================
; VBXE bring-up
;=============================================================================
; detect_vbxe : probe the VBXE at BOTH documented bases ($D600 then $D700).
;   The engine is assembled for $D600 (absolute VBXE_* addressing), so it can
;   only RUN at $D600 -- but we still probe $D700 to tell "wrong base" apart
;   from "no VBXE" so the caller can report it precisely (see vbxe_err).
;   Check per VBXE docs: CORE_VERSION ($40)==$10 AND (MINOR_REV ($41) & $70)>=$20.
;   Returns:  C=0          -> VBXE at $D600, OK to run
;             C=1, A=$01   -> VBXE present but at $D700 (unsupported base)
;             C=1, A=$00   -> no VBXE at either base
.proc detect_vbxe
        lda VBXE_VCTL               ; --- $D600 : the base the engine is built for
        cmp #CORE_FX_1XX
        bne ?try700
        lda VBXE_XDLA0
        and #MINOR_MASK
        cmp #MINOR_V120
        bcc ?try700
        clc                         ; FX core v1.2x+ at $D600 -> run
        rts
?try700 lda VBXE_VCTL_ALT           ; --- not at $D600 : probe $D700 for a precise error
        cmp #CORE_FX_1XX
        bne ?none
        lda VBXE_XDLA0_ALT
        and #MINOR_MASK
        cmp #MINOR_V120
        bcc ?none
        lda #$01                     ; present at $D700 -> unsupported base
        sec
        rts
?none   lda #$00                     ; nothing at either base
        sec
        rts
.endp

;-----------------------------------------------------------------------------
; detect_cpu : pick the render detail level. Rapidus is a 65C816 and clears the
; whole frame inside the vblank pace at full detail, so it renders every scanline.
; A stock NMOS 6502 cannot keep the heaviest scene (arene) inside the pace, so it
; drops to HALF vertical resolution (draw even scanlines only, 2-line-tall spans
; -> ~35% less raster CPU, NO culling -- every polygon is still drawn).
; Test: SEP #$01 ($E2,$01) sets the carry on a 65C816 (SEP works in the emulation
; mode the CPU boots in); on a 6502/65C02 $E2 is a stable 2-byte NOP and the carry
; stays clear from the CLC. Result -> poly_bcb_h (0 = full, 1 = half); see equates.
;-----------------------------------------------------------------------------
.proc detect_cpu
        clc
        .byte $E2,$01               ; SEP #$01 (816: C=1) / NOP #imm (6502: C unchanged=0)
        lda #0                      ; 65C816 (Rapidus) -> full detail
        bcs ?set
        lda #1                      ; stock 6502 -> half vertical res
?set    sta poly_bcb_h
        rts
.endp

.proc setup_memac
        lda #MEMW_HI | MC_CPU | MC_4K
        sta VBXE_MEMAC_CTL
        lda #0
        sta VBXE_MEMAC_B            ; data window off (set_poly_ptr/pl_byte re-enable)
        lda #BANK_EN | CTRL_BANK
        sta VBXE_BANK_SEL
        rts
.endp

.proc setup_xdls
        lda #0
        sta VBXE_VCTL
        ldx #0
?pg     txa
        asl @
        asl @
        asl @
        asl @
        asl @
        asl @
        clc
        adc #<MEMW
        sta dptr
        lda #>MEMW
        adc #0
        sta dptr+1
        ldy #xdl_tmpl_len-1
?cp     lda xdl_tmpl,y
        sta (dptr),y
        dey
        bpl ?cp
        ldy #8                      ; OVADR hi byte = page index (offset 8 in xdl_tmpl)
        txa
        sta (dptr),y
        inx
        cpx #4
        bne ?pg
        lda #0
        jsr show_page
        rts
.endp

xdl_tmpl
        ; top overscan: 20 lines, overlay OFF -> the border shows COLBK (black).
        ; Without this band the overlay starts at the first scanline and the area
        ; below the 200-line picture froze on the last overlay line (a palette
        ; colour) instead of the border -> a coloured band under the picture.
        dta XDLC_OVOFF | XDLC_RPTL
        dta $00
        dta 19                              ; repeat 19 -> 20 border lines
        ; main overlay band : 200 lines, palette #1, priority over all, END
        dta XDLC_GMON | XDLC_MAPOFF | XDLC_RPTL | XDLC_OVADR
        dta XDLC_ATT | XDLC_END | XDL_PXMODE
        dta SCRH-1
        dta $00,$00,$00                     ; OVADR (hi byte = page index, patched at offset 8)
        dta a(SCRW)
        dta OV_NORMAL | OV_PAL1
        dta PRI_ALL
xdl_tmpl_len equ *-xdl_tmpl

.proc show_page
        asl @
        asl @
        asl @
        asl @
        asl @
        asl @
        sta VBXE_XDLA0
        lda #$00
        sta VBXE_XDLA1
        lda #[CTRL>>16]
        sta VBXE_XDLA2
        rts
.endp

.ifdef HIRES_CAP
; set_render_mode : A = 0 -> LR 160 ; A != 0 -> SR 320. Stores `hires` and patches the
;   pixel-mode byte (+4) and per-line width word (+9/+10) in all 4 XDLs (each at
;   MEMW + page*$40, MEMAC-A control bank permanently mapped at $8000). A 320-wide page
;   (64000 B) still fits its own 64K VRAM region, so only width+pxmode change, not OVADR.
;   Used so the access-code part (16008) shows readable 320 letters; gameplay stays LR.
.proc set_render_mode
        sta hires
        bne ?sr
        lda cpu_detail              ; LR parts: restore CPU-detected vertical detail
        sta poly_bcb_h
        ldx #0
?lp     txa
        asl @
        asl @
        asl @
        asl @
        asl @
        asl @                       ; X*$40 = XDL[X] offset in the window
        tay
        lda #LR_PXMODE
        sta MEMW+4,y
        lda #<SCRW
        sta MEMW+9,y
        lda #>SCRW
        sta MEMW+10,y
        inx
        cpx #4
        bne ?lp
        rts
?sr     lda #0                      ; SR (access-code 16008): force FULL vertical detail
        sta poly_bcb_h              ;   (= Rapidus) even on stock 6502 -> readable letters
        ldx #0
?sp     txa
        asl @
        asl @
        asl @
        asl @
        asl @
        asl @
        tay
        lda #SR_PXMODE
        sta MEMW+4,y
        lda #<HR_SR_W
        sta MEMW+9,y
        lda #>HR_SR_W
        sta MEMW+10,y
        inx
        cpx #4
        bne ?sp
        rts
.endp
.endif

.proc upload_bcb
        ldx #BCB_SIZE-1
?f      lda bcb_tmpl,x
        sta BCB,x
        dex
        bpl ?f
        rts
.endp

bcb_tmpl
        dta $00,$00,$00             ; 0  src addr
        dta a(SCRW)                ; 3  src step Y
        dta $01                    ; 5  src step X
        dta $00,$00,$00            ; 6  dst addr   (PATCHED)
        dta a(SCRW)                ; 9  dst step Y
        dta $01                    ; 11 dst step X
        dta a($0000)               ; 12 width-1    (PATCHED)
        dta $00                    ; 14 height-1   (PATCHED)
        dta $00                    ; 15 AND mask   (PATCHED)
        dta $00                    ; 16 XOR mask   (PATCHED)
        dta $00                    ; 17 collision
        dta $00                    ; 18 zoom
        dta $00                    ; 19 pattern
        dta BLT_COPY               ; 20 control    (PATCHED)

;=============================================================================
; Palette
;=============================================================================
; Clear only VBXE palette #1 (the overlay palette; set_palette reloads its 16
; entries per scene). DO NOT TOUCH palette #0 -- it is the SYSTEM/GTIA palette
; that VBXE recolours the normal Atari passthrough through (incl. the OS screen,
; U1MB menu and IDE Plus BIOS). Zeroing it left every system screen black after
; exit/RESET until a COLD boot reloaded VBXE's default (the well-known VBXE bug;
; doom2d avoids it the same way -- writes only PSEL=1, never palette 0).
; The border is kept black via COLBK=0 + the overlay-off top XDL band, NOT by
; destroying palette 0 (its default index 0 is black).
.proc pal_init_black
        lda #1
        sta VBXE_PSEL
        lda #0
        sta VBXE_CSEL
        ldx #0
?l      lda #0
        sta VBXE_CR
        sta VBXE_CG
        sta VBXE_CB
        inx
        bne ?l
        rts
.endp

; set_palette(A = palette index 0..31) : load 16 RGB triples into VBXE pal #1.
.proc set_palette
        sta zp_n
        lda zp_n
        sta acc_lo
        lda #0
        sta acc_hi
        ldx #4
?s16    asl acc_lo
        rol acc_hi
        dex
        bne ?s16                    ; acc = N*16
        lda acc_lo
        sta t_lo
        lda acc_hi
        sta t_hi
        asl acc_lo
        rol acc_hi                  ; acc = N*32
        lda acc_lo
        clc
        adc t_lo
        sta acc_lo
        lda acc_hi
        adc t_hi
        sta acc_hi                  ; acc = N*48
        lda #<pal_data
        clc
        adc acc_lo
        sta pal_ptr
        lda #>pal_data
        adc acc_hi
        sta pal_ptr+1
        lda #1
        sta VBXE_PSEL
        lda #0
        sta VBXE_CSEL
        ; intro_pal.bin holds 7-bit channels; VBXE colour registers are 8-bit,
        ; so shift left 1 (== aw_play's d<<1 / the aw_sim oracle value).
        ldy #0
        ldx #16
?col    lda (pal_ptr),y
        asl @
        sta VBXE_CR
        iny
        lda (pal_ptr),y
        asl @
        sta VBXE_CG
        iny
        lda (pal_ptr),y
        asl @
        sta VBXE_CB
        iny
        dex
        bne ?col
        rts
.endp

;=============================================================================
; Page helpers + blitter primitives
;=============================================================================
.proc set_cbase_cur
        lda #0
        sta cbase
        sta cbase+1
        lda cur_draw
        sta cbase+2
        rts
.endp

.proc clear_page                    ; A = page, X = colour
        pha
        jsr blit_idle               ; idle before editing the BCB (X=colour kept)
        lda #0
        sta BCB+BCB_DST_ADDR
        sta BCB+BCB_DST_ADDR+1
        pla
        sta BCB+BCB_DST_ADDR+2
.ifdef HIRES_CAP
        ldy hires
        beq ?cplr
        lda #<(HR_SR_W-1)           ; SR : 320-wide page
        sta BCB+BCB_WIDTH
        lda #>(HR_SR_W-1)
        sta BCB+BCB_WIDTH+1
        lda #<HR_SR_W               ; row stride 320 (clear relies on DST_STEPY)
        sta BCB+BCB_DST_STEPY
        lda #>HR_SR_W
        sta BCB+BCB_DST_STEPY+1
        jmp ?cpw
?cplr   lda #<(SCRW-1)
        sta BCB+BCB_WIDTH
        lda #>(SCRW-1)
        sta BCB+BCB_WIDTH+1
        lda #<SCRW
        sta BCB+BCB_DST_STEPY
        lda #>SCRW
        sta BCB+BCB_DST_STEPY+1
?cpw
.else
        lda #<(SCRW-1)
        sta BCB+BCB_WIDTH
        lda #>(SCRW-1)
        sta BCB+BCB_WIDTH+1
.endif
        lda #SCRH-1
        sta BCB+BCB_HEIGHT
        lda #0
        sta BCB+BCB_AND
        stx BCB+BCB_XOR
        lda #BLT_COPY
        sta BCB+BCB_CTRL
        lda #$FF                     ; this clobbered the span BCB's mode fields
        sta last_scol                ;   -> force fill_span to re-patch its mode
        jsr fire_fill
        rts
.endp

.proc copy_page                     ; cp_src -> cp_dst (full page)
        jsr blit_idle               ; idle before editing the BCB
        lda #0
        sta BCB+BCB_SRC_ADDR
        sta BCB+BCB_SRC_ADDR+1
        lda cp_src
        sta BCB+BCB_SRC_ADDR+2
        lda #0
        sta BCB+BCB_DST_ADDR
        sta BCB+BCB_DST_ADDR+1
        lda cp_dst
        sta BCB+BCB_DST_ADDR+2
.ifdef HIRES_CAP
        ldy hires
        beq ?cylr
        lda #<HR_SR_W               ; SR : 320 src+dst stride
        sta BCB+BCB_SRC_STEPY
        sta BCB+BCB_DST_STEPY
        lda #>HR_SR_W
        sta BCB+BCB_SRC_STEPY+1
        sta BCB+BCB_DST_STEPY+1
        lda #1
        sta BCB+BCB_SRC_STEPX
        lda #<(HR_SR_W-1)
        sta BCB+BCB_WIDTH
        lda #>(HR_SR_W-1)
        sta BCB+BCB_WIDTH+1
        jmp ?cyw
?cylr   lda #<SCRW
        sta BCB+BCB_SRC_STEPY
        sta BCB+BCB_DST_STEPY
        lda #>SCRW
        sta BCB+BCB_SRC_STEPY+1
        sta BCB+BCB_DST_STEPY+1
        lda #1
        sta BCB+BCB_SRC_STEPX
        lda #<(SCRW-1)
        sta BCB+BCB_WIDTH
        lda #>(SCRW-1)
        sta BCB+BCB_WIDTH+1
?cyw
.else
        lda #<SCRW
        sta BCB+BCB_SRC_STEPY
        lda #>SCRW
        sta BCB+BCB_SRC_STEPY+1
        lda #1
        sta BCB+BCB_SRC_STEPX
        lda #<(SCRW-1)
        sta BCB+BCB_WIDTH
        lda #>(SCRW-1)
        sta BCB+BCB_WIDTH+1
.endif
        lda #SCRH-1
        sta BCB+BCB_HEIGHT
        lda #$FF
        sta BCB+BCB_AND
        lda #0
        sta BCB+BCB_XOR
        lda #BLT_COPY
        sta BCB+BCB_CTRL
        lda #$FF                     ; copy clobbered the span BCB mode fields
        sta last_scol
        jsr fire_fill
        rts
.endp

; fill_span : one horizontal run on the current draw page.
;   scol < $10  : solid colour
;   scol = $10  : transparent (dest |= 8)  via BLT_OR
;   scol > $10  : copy the same pixels from page 0 (background shows through)
.proc fill_span
        ; --- address + width math FIRST : touches only ZP scratch, so it runs
        ;     CONCURRENTLY with the still-running previous blit (pipelining). ---
        ldx sy                      ; offset = row_lut[sy] + sx
.ifdef HIRES_CAP
        ldy hires
        beq ?lrlut
        lda row_lo2,x               ; SR : y*320-$8000 LUT
        clc
        adc sx_lo
        sta zp_dlo
        lda row_hi2,x
        adc sx_hi
        sta zp_dmid
        jmp ?lutok
?lrlut
.endif
        lda row_lo,x
        clc
        adc sx_lo
        sta zp_dlo
        lda row_hi,x
        adc sx_hi
        sta zp_dmid
.ifdef HIRES_CAP
?lutok
.endif
        ; --- ONLY NOW wait for the blitter, then edit the BCB ---
?bw     lda VBXE_BL_BUSY            ; inlined blit_idle (saves the jsr/rts) --
        bne ?bw                     ;   the BCB must be idle before editing
        ; dst low/mid = offset (cbase low/mid are 0).  DST_ADDR+2 (page) and HEIGHT
        ; are set ONCE per shape in op_drawpoly -- constant for every span -- so
        ; they are NOT rewritten here.
        lda zp_dlo
        sta BCB+BCB_DST_ADDR
        lda zp_dmid
        sta BCB+BCB_DST_ADDR+1
        lda slen_lo                 ; emit_span already delivers WIDTH-1
        sta BCB+BCB_WIDTH
        lda slen_hi
        sta BCB+BCB_WIDTH+1
        ; --- colour mode (cache: AND/XOR/CTRL only change when scol changes) ---
        lda scol
        cmp #$11
        bcs ?copy                   ; copy mode: src changes per span -> always patch
        cmp last_scol
        beq ?fire                   ; same solid/transparent colour -> BCB mode is set
        sta last_scol
        cmp #$10
        beq ?transp
        jmp ?solid
?copy   ; copy from page 0 : src = offset (page 0 base = 0) -- patched every span
        lda zp_dlo
        sta BCB+BCB_SRC_ADDR
        lda zp_dmid
        sta BCB+BCB_SRC_ADDR+1
        lda #0
        sta BCB+BCB_SRC_ADDR+2
        lda #1
        sta BCB+BCB_SRC_STEPX
        lda #$FF
        sta BCB+BCB_AND
        lda #0
        sta BCB+BCB_XOR
        lda #BLT_COPY
        sta BCB+BCB_CTRL
        lda #$11                    ; mark mode=copy so a later solid/transp re-patches
        sta last_scol
        jmp ?fire
?transp lda #0
        sta BCB+BCB_AND
        lda #$08
        sta BCB+BCB_XOR
        lda #BLT_OR
        sta BCB+BCB_CTRL
        jmp ?fire
?solid  lda #0
        sta BCB+BCB_AND
        lda scol
        sta BCB+BCB_XOR
        lda #BLT_COPY
        sta BCB+BCB_CTRL
?fire   lda #1                      ; inlined fire_fill (start, NO wait -- the next
        sta VBXE_BL_START           ;   BCB edit is gated by its own leading idle)
        rts
.endp

; fire_fill : start the blitter and RETURN IMMEDIATELY (no wait). BL_ADR is loaded
;   once at init (always BCBF_V), so per fire we only write BL_START. The blit then
;   runs CONCURRENTLY while the CPU walks the next span's edges; the next BCB edit
;   is gated by that routine's own leading blit_idle, so the BCB is never touched
;   mid-blit (the dropped-polygon hazard stays covered). show_page is gated too.
.proc fire_fill
        lda #1
        sta VBXE_BL_START
        rts
.endp

; blit_idle : spin until the blitter is idle. Called BEFORE every BCB edit
;   (the blitter reads the BCB from VRAM while it runs, so modifying it mid-blit
;   corrupts the in-flight span -- the cause of dropped small polygons). Also
;   called before show_page so a displayed page is fully blitted.
.proc blit_idle
?w      lda VBXE_BL_BUSY
        bne ?w
        rts
.endp

.proc wait_vblank
        lda RTCLOK3
?w      cmp RTCLOK3
        beq ?w
        rts
.endp

; Row-offset table : row_lo/row_hi[y] = y * SCRW (the byte offset of scanline y).
; Replaces fill_span's per-span asl/rol multiply with one indexed read (#3).
; X is biased by $8000 (so the edge compare/clip is unsigned); that bias survives
; into sx (ROWBIAS), so pre-bias the table to cancel it: row_lut[sy] + sx = offset.
row_lo  :SCRH dta <((#*SCRW-ROWBIAS)&$FFFF)
row_hi  :SCRH dta >((#*SCRW-ROWBIAS)&$FFFF)
.ifdef HIRES_CAP
row_lo2 :SCRH dta <((#*HR_SR_W-$8000)&$FFFF)   ; SR(320): y*320, pre-biased by -$8000
row_hi2 :SCRH dta >((#*HR_SR_W-$8000)&$FFFF)
.endif

; Reciprocal table recip[dy] = round(65536/dy) for the edge-slope multiply (256
; entries: low bytes then high bytes). dy 0/1 unused (dy>=1; dy==1 special).
recip_lo
        ins 'out/recip.bin', 0, 256
recip_hi
        ins 'out/recip.bin', 256, 256
