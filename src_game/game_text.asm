;=============================================================================
; game_text.asm - GAME op_drawstring (bytecode opcode 0x12) : DRAWTEXT.
;   Renders the AW string table (game_text_data.inc) through the 8x8 font, the
;   same glyph blitter the intro uses (src/aw_text.asm), but driven by the game
;   bytecode fetch instead of the flattened playlist, and exiting to vm_cont.
;
;   This is what shows the death / ACCESS-CODE screen ("PRESS BUTTON OR RETURN
;   TO CONTINUE", "ACCESS CODE: ...") -- without it the restart screen is blank
;   black and the game looks like it never restarts.
;
;   ZP note: the intro's txt_ptr ($B3) is REUSED by the game as pl_bank (the
;   MEMAC-B bytecode-fetch bank), so the string pointer here is gtxt_ptr = cr0
;   ($C0). That's safe: op_drawstring renders via emit_span/fill_span, NOT the
;   polygon raster, so the cr0/cr1 edge accumulators are dead during text; the
;   next polygon's fill_poly_int reinitialises them. (Same union idea as aw4.)
;=============================================================================
gtxt_ptr = cr0                       ; $C0-$C1 : string byte ptr (txt_ptr collides w/ pl_bank)

; .proc scopes the ?local labels (?done/?scan/?char...) so they don't leak into the
; global '?'-namespace and steal op_shl/op_shr's `beq ?done` (which MADS would then bind
; to this far ?done -> branch-out-of-range). Entered by `jmp do_drawstring` from the optab.
.proc do_drawstring                  ; 0x12 : strId(word, big-endian) x(b) y(b) col(b)
        jsr vm_w                     ; strId : vm_s2 = hi, vm_s1 = lo
        lda vm_s1
        sta t_sidlo
        lda vm_s2
        sta t_sidhi
        mfetch
        sta txt_x
        mfetch
        sta txt_y
        mfetch
        sta txt_col
        ; find table index for strId (linear scan; aw_nstr entries)
        ldx #0
?scan   cpx #aw_nstr
        bcc *+5                      ; in-range; else jmp (?done is >127 B away)
        jmp ?done                    ; not found -> skip
        lda aw_id_lo,x
        cmp t_sidlo
        bne ?nx
        lda aw_id_hi,x
        cmp t_sidhi
        beq ?found
?nx     inx
        bne ?scan
?found  lda aw_str_lo,x              ; gtxt_ptr = aw_strbytes + offset[x]
        clc
        adc #<aw_strbytes
        sta gtxt_ptr
        lda aw_str_hi,x
        adc #>aw_strbytes
        sta gtxt_ptr+1
        ; glyph spans go through fill_span (like polygons), so set the same BCB
        ; constants op_drawpoly does -- page (DST+2) and HEIGHT=0.
        jsr blit_idle
        lda cbase+2
        sta BCB+BCB_DST_ADDR+2
        lda #0
        sta BCB+BCB_HEIGHT
        lda txt_col                  ; PERF: the text colour is constant for the whole string,
        sta poly_color               ;   so set poly_color/scol ONCE here instead of reloading
        sta scol                     ;   them per run in emit_run.
        lda txt_x
        sta t_cx
        jsr set_t_cbx                ; PERF: t_cbx = t_cx*8 computed once per LINE here; ?adv
                                     ;   adds 8 per glyph, so the *8 shift chain is out of the
                                     ;   per-glyph path (see set_t_cbx below).
?char   ldy #0
        lda (gtxt_ptr),y
        bne ?notend
        jmp ?done                    ; 0x00 terminator
?notend inc gtxt_ptr                 ; ptr++ (16-bit)
        bne ?p1
        inc gtxt_ptr+1
?p1     cmp #$0A
        bne ?glyph
        lda txt_y                    ; newline : y += 8, cx = start x
        clc
        adc #8
        sta txt_y
        lda txt_x
        sta t_cx
        jsr set_t_cbx                ; reset t_cbx = t_cx*8 for the new line
        jmp ?char
?glyph  sta t_ch
        lda t_cx
        cmp #40
        bcs ?adv                     ; column >= 40 : off the right, don't draw
        jsr draw_glyph
?adv    inc t_cx                     ; advance column AND t_cbx += 8 (next glyph's 320-space
        lda t_cbx                    ;   base col) -- keeps t_cbx in sync with no recompute;
        clc                          ;   runs even on the skipped (col>=40) path, staying synced
        adc #8
        sta t_cbx
        bcc ?adv1
        inc t_cbx+1
?adv1   jmp ?char
?done   jmp vm_cont
.endp

; draw_glyph : render glyph t_ch at column t_cx, row txt_y, colour txt_col.
.proc draw_glyph
        lda t_ch                     ; fp = aw_font + (ch-0x20)*8
        sec
        sbc #$20
        sta t_fp
        lda #0
        sta t_fp+1
        asl t_fp
        rol t_fp+1
        asl t_fp
        rol t_fp+1
        asl t_fp
        rol t_fp+1
        lda t_fp
        clc
        adc #<aw_font
        sta t_fp
        lda t_fp+1
        adc #>aw_font
        sta t_fp+1
        ; t_cbx (= cx*8, the 320-space base column) is maintained by do_drawstring now --
        ; computed once per line in set_t_cbx, advanced by +8 per glyph at ?adv -- so the old
        ; per-glyph *8 shift chain that lived here is gone (~25-30 cyc saved per drawn glyph).
        lda #0
        sta t_j
?row    lda txt_y                    ; py = txt_y + j
        clc
        adc t_j
        cmp #SCRH
        bcs ?nextrow                 ; py >= 200 -> skip this row
        sta sy                       ; fill_span row
        ldy t_j                      ; rowbits = font[fp + j]
        lda (t_fp),y
        sta t_rbits
        lda #0
        sta t_inrun
        ldx #0                       ; i = bit index 0..7 (MSB first)
?bit    asl t_rbits                  ; carry = pixel i
        bcc ?clr
        lda t_inrun
        bne ?bnext                   ; already inside a run
        stx t_i0                     ; start a run at i
        lda #1
        sta t_inrun
        jmp ?bnext
?clr    lda t_inrun
        beq ?bnext                   ; not in a run
        jsr emit_run                 ; close run [t_i0 .. i-1] (X = i)
        lda #0
        sta t_inrun
?bnext  inx
        cpx #8
        bne ?bit
        lda t_inrun                  ; trailing run open to the row edge?
        beq ?nextrow
        jsr emit_run                 ; X = 8 -> i1 = 7
?nextrow
        inc t_j
        lda t_j
        cmp #8
        bne ?row
        rts
.endp

; set_t_cbx : t_cbx = t_cx * 8 (the glyph's 320-space base column). Called once per LINE
;   (string start + each newline); the per-glyph ?adv step then just adds 8. Hoisting this
;   *8 shift chain out of draw_glyph's per-glyph path saves ~25-30 cyc per drawn glyph.
;   (game_text now lives at $0900 with ~1.7 KB headroom, so this extra proc fits easily.)
.proc set_t_cbx
        lda t_cx
        sta t_cbx
        lda #0
        sta t_cbx+1
        asl t_cbx
        rol t_cbx+1
        asl t_cbx
        rol t_cbx+1
        asl t_cbx
        rol t_cbx+1
        rts
.endp

; emit_run : draw the run [t_i0 .. X-1] (320-space cols cbx+i0 .. cbx+i1) on the
;   current row (sy preset) in colour txt_col, via emit_span (LR x>>1). Preserves X.
.proc emit_run
        lda t_cbx                    ; a = cbx + i0 + $8000  (bias to match emit_span)
        clc
        adc t_i0
        sta a_lo
        lda t_cbx+1
        adc #$80
        sta a_hi
        txa                          ; b = cbx + (X-1) + $8000
        sec
        sbc #1
        clc
        adc t_cbx
        sta b_lo
        lda t_cbx+1
        adc #$80
        sta b_hi
        ; poly_color/scol are set ONCE per string in do_drawstring now (text colour is
        ; constant across the whole string), so emit_run no longer touches them per run.
        txa
        pha                          ; fill_span clobbers X (ldx sy) -> save it
        jsr emit_span
        pla
        tax
        rts
.endp

;=============================================================================
; draw_loading : paint a "LOADING..." screen on the currently-displayed page while
;   load_part streams the next part off the disk. The SIO read freezes the VM for
;   ~1-3 s, and the stream only overwrites the shape/code/sound banks + pal_data --
;   NOT the framebuffer pages -- so a screen drawn here survives the whole read.
;
;   Two VBXE palette-1 entries are forced (idx0 black, idx1 white) so the text is
;   legible whatever palette the scene we are leaving had applied; the visible page
;   is blacked (this also wipes any LR<->SR mode-switch garble) and the string is
;   printed centred. The new part's first op_updatedisplay reloads pal #1 (set_palette)
;   and redraws, which clears this screen -- so no teardown is needed (gameplay
;   already runs op_drawstring through the same path).
;
;   Renders via the same glyph path as do_drawstring (draw_glyph/emit_run/fill_span),
;   honouring `hires` (works in both LR gameplay and the SR access-code mode). It
;   touches only the blitter + the text scratch, never the MEMAC-B window, so it is
;   safe to run before load_part sets that window up for the stream. .proc isolates
;   its ? labels (so ?char/?done don't clash with do_drawstring's).
;=============================================================================
LD_COL  = 1                          ; text colour index (forced white below)
LD_X    = 15                         ; start column (40 cols of 8 px) -> "LOADING..." centred
LD_Y    = 96                         ; row in px (200-tall page)

.proc draw_loading
        lda #1                       ; pal #1: idx0 = black bg, idx1 = white text
        sta VBXE_PSEL                ;   (CSEL auto-increments on each CB write)
        lda #0
        sta VBXE_CSEL
        sta VBXE_CR
        sta VBXE_CG
        sta VBXE_CB                  ; idx0 : black
        lda #$F0
        sta VBXE_CR
        sta VBXE_CG
        sta VBXE_CB                  ; idx1 : white
        lda vm_cur2                  ; blank the displayed page to idx0 (black)
        ldx #0
        jsr clear_page
        ; glyph BCB: draw to the displayed page, 1-tall spans (full vertical detail),
        ; constant colour for the whole string. clear_page left last_scol = $FF, so the
        ; first fill_span re-patches its mode fields to the solid LD_COL.
        jsr blit_idle
        lda vm_cur2
        sta BCB+BCB_DST_ADDR+2
        lda #0
        sta BCB+BCB_HEIGHT
        lda #LD_COL
        sta poly_color
        sta scol
        lda #<ld_str
        sta gtxt_ptr
        lda #>ld_str
        sta gtxt_ptr+1
        lda #LD_Y
        sta txt_y
        lda #LD_X
        sta t_cx
        jsr set_t_cbx                ; t_cbx = t_cx*8 (320-space base column)
?char   ldy #0
        lda (gtxt_ptr),y
        beq ?done                    ; 0x00 terminator
        inc gtxt_ptr
        bne ?p
        inc gtxt_ptr+1
?p      sta t_ch
        lda t_cx
        cmp #40
        bcs ?adv                     ; column >= 40 : off the right edge, don't draw
        jsr draw_glyph
?adv    inc t_cx                     ; advance column AND t_cbx += 8 (next 320-space base col)
        lda t_cbx
        clc
        adc #8
        sta t_cbx
        bcc ?char
        inc t_cbx+1
        jmp ?char
?done   jsr blit_idle                ; let the last glyph land before we show the page
        lda vm_cur2
        jsr show_page                ; re-assert the displayed page (now the LOADING screen)
        rts
ld_str  dta c'LOADING...',0
.endp
