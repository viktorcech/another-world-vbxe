;=============================================================================
; aw_settings.asm - pre-intro sound-output select (POKEY 4-bit vs COVOX 8-bit).
;
;   Called once from `start`, after font_init + snd_init, before `replay`. At that
;   point the VBXE overlay is ON, page 0 is the displayed page (cleared black),
;   the font glyph cells are in VRAM, and the sound IRQ is hooked but idle.
;
;   Draws a small menu to page 0 and blocks on the console keys:
;     SELECT = toggle POKEY/COVOX   START = begin the intro
;   The selection is shown by COLOUR (selected = yellow idx2, other = white idx1)
;   -- no moving cursor: BSTENCIL skips src==0, so re-drawing the same option line
;   in a new colour overwrites exactly the glyph pixels (the shape is identical),
;   cleanly recolouring it. On START it calls snd_set_mode(set_sel) and returns.
;
;   Reuses the text engine (draw_glyph / line_clip / the glyph BCB) from
;   aw_text.asm and the page/palette helpers from aw_vbxe.asm. Runs entirely
;   before the first 0x07 op, so it is free to use the op_drawtext scratch
;   (txt_ptr / txt_x / txt_y / txt_col / t_cx / t_ch / t_vis / t_gh). Each routine
;   is a .proc so its `?` temp labels stay isolated (mads scopes them per-proc;
;   non-proc `?` labels share one global area and would clash with op_drawtext).
;=============================================================================
CONSOL  = $D01F                      ; bit0 START, bit1 SELECT (0 = pressed); write bit3 = speaker
OPT_X   = 6                          ; option text column (0..39, 8 px each)
OPT_Y0  = 88                         ; POKEY row (px)
OPT_Y1  = 104                        ; COVOX row (px)

.proc snd_settings
        lda #8
        sta CONSOL                   ; speaker off, console keys readable
        lda #0                       ; draw to page 0 (already the displayed page)
        sta cur_draw
        jsr set_cbase_cur
        ldx #0                       ; clear page 0 to colour index 0 (background)
        lda #0
        jsr clear_page
        jsr set_settings_pal         ; pal #1: idx0 bg / idx1 white / idx2 yellow
        jsr text_setup               ; glyph BCB constants (clear_page clobbered them)
        jsr draw_statics
        jsr draw_options
        lda #0
        jsr show_page
?loop   jsr read_console             ; A = 1 SELECT / 2 START (blocks until a press)
        cmp #2
        beq ?begin
        lda set_sel                  ; SELECT -> toggle + redraw the two options
        eor #1
        sta set_sel
        jsr draw_options
        jmp ?loop
?begin  jsr blit_idle                ; restore the span BCB fields the replayer expects:
        lda #<SCRW                   ;   text_setup left SRC_STEPY = 4 (glyph stride); a
        sta BCB+BCB_SRC_STEPY        ;   later 2-tall copy span would read row 2 from src+4
        lda #>SCRW                   ;   instead of src+SCRW (mirror op_drawtext's teardown)
        sta BCB+BCB_SRC_STEPY+1
        lda #$FF                     ; force the next fill_span to re-patch its mode fields
        sta last_scol
        lda set_sel
        jsr snd_set_mode             ; apply the chosen output mode
        rts
.endp

;-----------------------------------------------------------------------------
; text_setup : BCB constants for the glyph BSTENCIL blits (copied from
;   op_drawtext; the per-string colour is set in draw_str). DST/SRC step Y and
;   page are valid because clear_page left DST_STEPY = SCRW and cbase = page 0.
;-----------------------------------------------------------------------------
.proc text_setup
        jsr blit_idle
        lda cbase+2
        sta BCB+BCB_DST_ADDR+2
        lda #[FONT_V>>16]
        sta BCB+BCB_SRC_ADDR+2
        lda #4                       ; src row stride = glyph cell width (4 LR bytes)
        sta BCB+BCB_SRC_STEPY
        lda #0
        sta BCB+BCB_SRC_STEPY+1
        sta BCB+BCB_WIDTH+1
        sta BCB+BCB_XOR
        lda #1
        sta BCB+BCB_SRC_STEPX
        lda #3                       ; WIDTH-1 : 8 px = 4 LR bytes
        sta BCB+BCB_WIDTH
        lda #BLT_BSTENCIL
        sta BCB+BCB_CTRL
        rts
.endp

;-----------------------------------------------------------------------------
; draw_str : render the $00-terminated string at txt_ptr, starting at column
;   txt_x / row txt_y, colour index txt_col. Reuses draw_glyph + line_clip.
;-----------------------------------------------------------------------------
.proc draw_str
        lda txt_col
        sta BCB+BCB_AND
        jsr line_clip                ; t_vis / t_gh from txt_y
        lda txt_x
        sta t_cx
?c      ldy #0
        lda (txt_ptr),y
        beq ?done
        inc txt_ptr
        bne ?p
        inc txt_ptr+1
?p      sta t_ch
        lda t_vis
        beq ?adv                     ; line below the screen : don't draw
        lda t_cx
        cmp #40
        bcs ?adv                     ; column >= 40 : off the right
        jsr draw_glyph
?adv    inc t_cx
        jmp ?c
?done   rts
.endp

;-----------------------------------------------------------------------------
; draw_statics : the 4 fixed (white) lines -- title, heading, and the two key
;   hints. (txt_ptr/x/y/col set per line, then jsr draw_str.)
;-----------------------------------------------------------------------------
.proc draw_statics
        lda #<s_title
        sta txt_ptr
        lda #>s_title
        sta txt_ptr+1
        lda #10
        sta txt_x
        lda #40
        sta txt_y
        lda #1
        sta txt_col
        jsr draw_str
        lda #<s_head
        sta txt_ptr
        lda #>s_head
        sta txt_ptr+1
        lda #10
        sta txt_x
        lda #64
        sta txt_y
        lda #1
        sta txt_col
        jsr draw_str
        lda #<s_sel
        sta txt_ptr
        lda #>s_sel
        sta txt_ptr+1
        lda #8
        sta txt_x
        lda #150
        sta txt_y
        lda #1
        sta txt_col
        jsr draw_str
        lda #<s_begin
        sta txt_ptr
        lda #>s_begin
        sta txt_ptr+1
        lda #8
        sta txt_x
        lda #166
        sta txt_y
        lda #1
        sta txt_col
        jsr draw_str
        rts
.endp

;-----------------------------------------------------------------------------
; draw_options : both option lines, every call. Selected = colour 2 (yellow),
;   the other = colour 1 (white). Re-drawing recolours in place (see header).
;-----------------------------------------------------------------------------
.proc draw_options
        lda #<s_pokey
        sta txt_ptr
        lda #>s_pokey
        sta txt_ptr+1
        lda #OPT_X
        sta txt_x
        lda #OPT_Y0
        sta txt_y
        ldx #1
        lda set_sel
        bne ?p0                      ; sel != 0 -> POKEY not selected -> white
        ldx #2
?p0     stx txt_col
        jsr draw_str
        lda #<s_covox
        sta txt_ptr
        lda #>s_covox
        sta txt_ptr+1
        lda #OPT_X
        sta txt_x
        lda #OPT_Y1
        sta txt_y
        ldx #1
        lda set_sel
        beq ?c0                      ; sel == 0 -> COVOX not selected -> white
        ldx #2
?c0     stx txt_col
        jsr draw_str
        rts
.endp

;-----------------------------------------------------------------------------
; read_console : block until START or SELECT, debounce on release.
;   Returns A = 1 (SELECT) or 2 (START). START wins if both are held.
;-----------------------------------------------------------------------------
.proc read_console
?w      lda CONSOL
        and #$03
        cmp #$03
        beq ?w                       ; nothing pressed -> wait
        lda CONSOL
        and #$01
        bne ?notst                   ; bit0 = 1 -> START not pressed -> SELECT
        jsr ?rel
        lda #2
        rts
?notst  jsr ?rel
        lda #1
        rts
?rel    lda CONSOL                   ; wait for release (debounce)
        and #$03
        cmp #$03
        bne ?rel
        rts
.endp

;-----------------------------------------------------------------------------
; set_settings_pal : VBXE palette #1 -- idx0 dark-blue bg, idx1 white, idx2
;   yellow. CSEL auto-increments on each CB write (like pal_init_black).
;-----------------------------------------------------------------------------
.proc set_settings_pal
        lda #1
        sta VBXE_PSEL
        lda #0
        sta VBXE_CSEL
        lda #$10
        sta VBXE_CR
        lda #$18
        sta VBXE_CG
        lda #$48
        sta VBXE_CB                  ; idx0 : dark-blue background
        lda #$F0
        sta VBXE_CR
        lda #$F0
        sta VBXE_CG
        lda #$F0
        sta VBXE_CB                  ; idx1 : white
        lda #$F0
        sta VBXE_CR
        lda #$D0
        sta VBXE_CG
        lda #$20
        sta VBXE_CB                  ; idx2 : yellow
        rts
.endp

;-----------------------------------------------------------------------------
; Data : selection state + strings (raw ASCII $20-$7F, $00-terminated -- matches
;   aw_strbytes; the font glyph index is byte-$20). Uppercase only (the AW font
;   is upper-case + digits). Kept outside the .procs (plain data labels).
;-----------------------------------------------------------------------------
set_sel dta 0                        ; 0 = POKEY, 1 = COVOX
s_title dta c'ANOTHER WORLD',0
s_head  dta c'SOUND OUTPUT',0
s_pokey dta c'POKEY  4-BIT',0
s_covox dta c'COVOX  8-BIT  D280',0
s_sel   dta c'SELECT = CHANGE',0
s_begin dta c'START  = BEGIN',0
