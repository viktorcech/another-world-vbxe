;=============================================================================
        org $2000

pace_due = $BA                      ; RTCLOK3 value when the next frame is DUE (deadline
                                    ; pacing: frame-to-frame = hold vblanks, render absorbed
                                    ; -- mirrors the game fix. $B8/$B9 = snd state, aw_sound.)
pace_frac = $BB                     ; NTSC speed-comp accumulator (fifths of a vblank)

start
        sei
        lda PORTB
        ora #$02                    ; disable BASIC, keep OS ROM
        sta PORTB
        lda #0
        sta SDMCTL                  ; ANTIC playfield DMA off (VBXE overlay covers all)
        sta DMACTL
        sta COLOR4                  ; black border (OVOFF overscan bands show COLBK)
        sta COLBK
        cli

        jsr detect_vbxe
        bcc ?ok
        jmp no_vbxe
?ok
        jsr detect_cpu              ; Rapidus(65C816) -> full ; stock 6502 -> half-res
        lda PAL                     ; read $D014 (NTSC=$0F, PAL=$01); corrected by eor below
        and #$0E
        eor #$0E                    ; FIX: $D014 reads NTSC=$0F, PAL=$01, so &$0E gives
                                    ;   $0E on NTSC / $00 on PAL -- INVERTED. eor flips it so
                                    ;   is_pal != 0 truly means PAL (pace skips comp on PAL).
        sta is_pal
        jsr setup_memac
        jsr setup_xdls
        jsr upload_bcb
        jsr pal_init_black

        ; the blitter list address is constant (the one BCB) -> load it ONCE,
        ; BEFORE any fire (the page clears below already fire the blitter);
        ; fire_fill then only writes BL_START per span.
        lda #<BCBF_V
        sta VBXE_BL_ADR0
        lda #>BCBF_V
        sta VBXE_BL_ADR1
        lda #[BCBF_V>>16]
        sta VBXE_BL_ADR2

        ; VRAM is NOT zeroed at power-on, but the playlist (from the VM, whose
        ; pages start black) assumes pages start black. Clear all 4 pages, else
        ; a scene that BLITs/copies a not-yet-filled page shows garbage VRAM.
        ldx #0                      ; colour 0 = black
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

        ; init the MEMAC-B bank cache (#2). setup_memac left MEMAC_B = $00.
        ; poly_bnk/pb_ptr need no seed: set_poly_ptr (re)loads them -- bank
        ; included -- before every poly stream.
        lda #0
        sta memb_cur
        lda #$FF                    ; span colour cache empty
        sta last_scol

        jsr font_init               ; expand the 8x8 font into VRAM glyph cells
                                    ;   (MEMAC-B on/off inside; MUST run before
                                    ;   snd_init -- the sound IRQ restores MEMAC_B
                                    ;   to memb_cur and would yank the bank mid-write)

        jsr snd_init                ; POKEY SFX player : hook Timer 1 IRQ (loading done)

.if DIAG
;-----------------------------------------------------------------------------
; ISOLATION TEST : draw ONLY group 4478 (the frame-132 character) on a cleared
; page, repeatedly, outside the playlist. If the legs/elevator appear here, the
; bug is in the playlist context (COPY / page state / streaming); if they are
; missing here too, it is in the 4478 render path on hardware.
;-----------------------------------------------------------------------------
        lda #4
        jsr set_palette             ; the character scene's palette
?diag
        ldx #6                      ; clear page 0 to colour 6 (visible bg)
        lda #0
        jsr clear_page
        lda #0
        sta cur_draw
        jsr set_cbase_cur
        ; TALL-FILL TEST: draw 5954 (elevator, top-level FILL 32x171) standalone.
        ; If it is missing here too, the bug is in fill_poly_int for tall polys.
        lda #<5954
        sta dr_off
        lda #>5954
        sta dr_off+1
        lda #14
        sta dr_x
        lda #0
        sta dr_x+1
        lda #80
        sta dr_y
        lda #0
        sta dr_y+1
        lda #64
        sta dr_zoom
        lda #0
        sta dr_zoom+1
        lda #$FF
        sta dr_col
        lda #0
        sta psp
        jsr set_poly_ptr            ; sync the stream ptr (poly_fetch is check-free)
        jsr poly_draw
        jsr wait_vblank
        lda #0
        jsr show_page
        jmp ?diag
.endif

;-----------------------------------------------------------------------------
; main : replay the playlist forever
;-----------------------------------------------------------------------------
replay
        lda #$FF                    ; no palette change pending across a restart
        sta pend_pal
        lda RTCLOK3                 ; pacing: first frame is due now
        sta pace_due
        lda #0
        sta pace_frac               ; NTSC speed-comp accumulator starts empty
        lda #0                      ; playlist stream ptr = start of $060000:
        sta pl_wlo                  ;   window $4000, bank $18 (PLAY_BANK0)
        lda #>DATAW
        sta pl_whi
        lda #$80+PLAY_BANK0
        sta pl_bnk
next_op
        jsr pl_byte
        cmp #$00
        beq ?fin                    ; END : stop the intro (was beq replay = loop)
        cmp #$01
        beq op_setpal
        cmp #$02
        beq op_selpage
        cmp #$03
        beq op_fillpage
        cmp #$04
        beq op_copypage
        cmp #$05
        beq ?dp
        cmp #$06
        beq op_blit
        cmp #$07
        beq ?txt
        cmp #$08
        beq ?snd
        jmp next_op
?dp     jmp op_drawpoly
?txt    jmp op_drawtext
?snd    jmp op_sound
?fin    jmp intro_done

;-----------------------------------------------------------------------------
; op_sound (0x08) : SOUND idx(1) -- fire the POKEY sample player (latest wins).
;-----------------------------------------------------------------------------
op_sound
        jsr pl_byte
        tax
        jsr snd_play
        jmp next_op

;=============================================================================
; Replayer opcode handlers
;=============================================================================
op_setpal
        jsr pl_byte                  ; defer: a slow render must not recolour the
        sta pend_pal                 ; still-displayed OLD frame. Apply at the BLIT
        jmp next_op                  ; (in vblank), atomic with the page swap.

op_selpage
        jsr pl_byte
        sta cur_draw
        jsr set_cbase_cur
        jmp next_op

op_fillpage
        jsr pl_byte
        pha
        jsr pl_byte
        tax
        pla
        jsr clear_page
        jmp next_op

op_copypage
        jsr pl_byte
        sta cp_src
        jsr pl_byte
        sta cp_dst
        jsr copy_page
        jmp next_op

op_blit
        jsr pl_byte
        sta blit_pg
        jsr pl_byte
        sta hold
        jsr blit_idle               ; last poly span fired without waiting -> finish it
        ; --- deadline pacing: spin until the frame is DUE (absorbs render time so the
        ;     frame interval is `hold` vblanks, not render+hold). Exit at a vblank tick
        ;     -> tear-free flip. (Old code waited hold vblanks AFTER the render.) ---
?wd     lda pace_due
        sec
        sbc RTCLOK3                 ; due - now (signed)
        beq ?due                    ; exactly due (at tick) -> show tear-free
        bpl ?wd                     ; due ahead -> spin
        jsr wait_vblank             ; overran -> sync to vblank so the flip is tear-free
?due    lda pend_pal                ; apply any deferred palette now (in vblank),
        bmi ?nopal                  ;   together with the page swap -> no dark flash
        jsr set_palette
        lda #$FF
        sta pend_pal
?nopal  lda blit_pg
        jsr show_page
        lda hold                    ; hold = host-frames this frame (>=1)
        bne ?h1
        lda #1
?h1     sta hold
        ; --- NTSC speed compensation (mirrors the game). AW data is 50 Hz; an NTSC
        ;     vblank is 60 Hz, so holding N vblanks runs 1.2x fast. Add hold/5 extra
        ;     vblanks (pace_frac = fifths carries the remainder). is_pal!=0 = PAL (skip).
        lda is_pal
        bne ?paced
        lda pace_frac
        clc
        adc hold
?f5     cmp #5
        bcc ?fdone
        sbc #5                      ; carry set by cmp>=5
        inc hold
        jmp ?f5
?fdone  sta pace_frac
?paced  lda hold
        clc                         ; pace_due += hold (NTSC-adjusted; drift-free cadence)
        adc pace_due
        sta pace_due
        sec
        sbc RTCLOK3                 ; resync if we fell behind (render overran)
        bpl ?bdone
        lda RTCLOK3
        clc
        adc hold
        sta pace_due
?bdone  jmp next_op

op_drawpoly
        jsr pl_byte
        sta dr_off
        jsr pl_byte
        sta dr_off+1
        jsr pl_byte
        sta dr_x
        jsr pl_byte
        sta dr_x+1
        jsr pl_byte
        sta dr_y
        jsr pl_byte
        sta dr_y+1
        jsr pl_byte
        sta dr_zoom
        jsr pl_byte
        sta dr_zoom+1
        ; per-shape zoom dispatch : dr_zoom is constant through the whole
        ; poly_draw tree, so pick read_scaled's path ONCE here (SMC operand)
        ; -- zoom==64 (1:1, ~97% of intro shapes) -> rs_fast, no per-coord mul.
        ldx #<rs_fast
        ldy #>rs_fast
        lda dr_zoom+1
        bne ?zslow
        lda dr_zoom
        cmp #64
        beq ?zset
?zslow  ldx #<rs_slow
        ldy #>rs_slow
?zset   stx rs_smc+1
        sty rs_smc+2
        lda #$FF                    ; top-level colour 0xFF -> use poly's own index
        sta dr_col
        lda #0
        sta psp                     ; reset recursion stack
        ; BCB constants for the whole shape : the dst page (DST_ADDR+2) and the
        ; span HEIGHT (poly_bcb_h: 0 full / 1 half-res) are identical for every
        ; fill_span of this polygon (no selpage/fill/copy can intervene inside one
        ; DRAWPOLY), so set them ONCE here instead of in every fill_span. blit_idle
        ; guards the BCB edit.
        jsr blit_idle
        lda cbase+2
        sta BCB+BCB_DST_ADDR+2
        lda poly_bcb_h              ; 0 = 1-tall spans (full) ; 1 = 2-tall (half-res, stock)
        sta BCB+BCB_HEIGHT
        jsr set_poly_ptr            ; dr_off just jumped -> sync the stream pointer
        jsr poly_draw
        jmp next_op
