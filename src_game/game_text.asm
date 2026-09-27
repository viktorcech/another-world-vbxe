;=============================================================================
; game_text.asm - GAME op_drawstring (bytecode opcode 0x12) : DRAWTEXT.
;   Renders the AW string table (game_text_data.inc) through the 8x8 font, the
;   same glyph blitter the intro uses (src/aw_text.asm), but driven by the game
;   bytecode fetch instead of the flattened playlist, and exiting to vm_fetch.
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
        mfetch0                      ; (Y = 0 after vm_w)
        sta txt_x
        mfetch0
        sta txt_y
        mfetch0
        sta txt_col
        ; find table index for strId (linear scan; aw_nstr entries)
        ldx #0
?scan   cpx #aw_nstr
        jcs ?done                    ; not found -> skip (a branch when in range)
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
        ; constants op_drawpoly does -- page (DST+2) and HEIGHT=0 (no wait: only
        ; the START waits).
        lda cbase+2
        sta BCB+BCB_DST_ADDR+2
        sta bcb_pg                   ; (the span BCB page / height shadow)
        lda #0
        sta BCB+BCB_HEIGHT
        sta bcb_ht
        lda txt_col                  ; PERF: the text colour is constant for the whole string,
        sta poly_color               ;   so set poly_color/scol ONCE here instead of reloading
        sta scol                     ;   them per run in emit_run.
.if 1
        ; ... and its BCB mode fields go in ONCE too, so every run of the string takes
        ; the same fused span path the polygons take (no colour test per run).
        ldx #$D0                     ; bne: solid / transparent -> the run just STARTs
        cmp #$11
        bcc ?tma
        ldx #$F0                     ; beq: copy mode (the source per run)
        lda #$11
?tma    stx draw_scanline_fast.dsf_m
.ifdef HIRES_CAP
        stx dsf_sr.dsr_m
.endif
        cmp last_scol
        beq ?tmok
        jsr span_mode                ; the mode fields for this colour
?tmok
.ifdef HIRES_CAP
        ldx hires                    ; the run's span: LR fused / SR fused
        lda er_tlo,x
        sta emit_run.er_go+1
        lda er_thi,x
        sta emit_run.er_go+2
.endif
.endif
        lda txt_x
        sta t_cx
        jsr set_t_cbx                ; PERF: t_cbx = t_cx*8 computed once per LINE here; ?adv
                                     ;   adds 8 per glyph, so the *8 shift chain is out of the
                                     ;   per-glyph path (see set_t_cbx below).
?char   ldy #0
        lda (gtxt_ptr),y
        beq ?done
        ;jmp ?done                    ; 0x00 terminator
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
?done   jmp vm_fetch                 ; drawstring never ends the slice -> straight back
.endp                                ;   to the fetch loop (vm_cont is gone, fps wave)

; draw_glyph : render glyph t_ch at column t_cx, row txt_y, colour txt_col.
.if 1
        nocross draw_glyph, draw_glyph.gl_row, draw_glyph.gl_end
.endif
.proc draw_glyph
.if 1
        lda t_ch                     ; fp = aw_font + n*8, n = ch-0x20 (8-bit)
        sec
        sbc #$20
        asl @                        ; n*8 as a 9-bit rotation through C (6502 skill:
        rol @                        ;   "five lsr for the top bits -> rol the other
        rol @                        ;   way"): A = n4..n0 0 n7 n6, C = n5
        tax
        and #3                       ; hi = n7 n6 n5 = n >> 5  (rol: C = 0 after, the
        rol @                        ;   top bit shifted out is 0)
        tay
        txa
        and #$F8                     ; lo = n << 3
        adc #<aw_font                ; (C = 0)
        sta t_fp
        tya
        adc #>aw_font
        sta t_fp+1
.else
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
.endif
        ; t_cbx (= cx*8, the 320-space base column) is maintained by do_drawstring now --
        ; computed once per line in set_t_cbx, advanced by +8 per glyph at ?adv -- so the old
        ; per-glyph *8 shift chain that lived here is gone (~25-30 cyc saved per drawn glyph).
.if 1
        ; The row's bits stay in A (asl @, not asl in memory) and the run state is the
        ; code position (?out / ?in), not a flag: a loop ends by itself when no set bit
        ; is left -- the zeros shifted in close a run open to the row edge at i = 8.
        ldy #0                       ; j = font row (t_j across the runs: emit_run
?row                                 ;   keeps X only)
gl_row  sty t_j
        tya
        clc
        adc txt_y                    ; py = txt_y + j
        cmp #SCRH
        bcs ?nextrow                 ; py >= 200 -> skip this row
        sta sy                       ; fill_span row
        lda (t_fp),y                 ; rowbits = font[fp + j], MSB = pixel 0
        beq ?nextrow
        ldx #$FF                     ; X = bit index i
?out    inx                          ; outside a run: to the next set bit (A != 0,
        asl @                        ;   so one is ahead)
        bcc ?out
        stx t_i0                     ; a run starts at i
?in     inx                          ; inside: to the next clear bit
        asl @
        bcs ?in
        sta t_rbits                  ; close [t_i0 .. X-1] (emit_run keeps X, not A)
        jsr emit_run
        lda t_rbits
        bne ?out                     ; set bits left
?nextrow
        ldy t_j
        iny                          ; skill-ok LOOPCP: the rows must go top -> bottom
        cpy #8                       ;   (py = txt_y + j and the span order follow j)
        bne ?row
gl_end  rts
.else
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
.endif
.endp

; set_t_cbx : t_cbx = t_cx * 8 (the glyph's 320-space base column). Called once per LINE
;   (string start + each newline); the per-glyph ?adv step then just adds 8. Hoisting this
;   *8 shift chain out of draw_glyph's per-glyph path saves ~25-30 cyc per drawn glyph.
;   (game_text now lives at $0900 with ~1.7 KB headroom, so this extra proc fits easily.)
.proc set_t_cbx
.if 1
        lda t_cx                     ; cx*8 as a 9-bit rotation through C (as in
        asl @                        ;   draw_glyph): A = c4..c0 0 c7 c6, C = c5
        rol @
        rol @
        tax
        and #$F8                     ; lo = cx << 3 (and keeps C)
        sta t_cbx
        txa
        and #3                       ; hi = c7 c6 c5 = cx >> 5
        rol @
        sta t_cbx+1
.else
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
.endif
        rts
.endp

.if 1
.ifdef HIRES_CAP
er_tlo  dta <draw_scanline_fast.es_lrf, <dsf_sr.es_srf   ; the fused span, LR / SR
er_thi  dta >draw_scanline_fast.es_lrf, >dsf_sr.es_srf
.endif
.endif

; emit_run : draw the run [t_i0 .. X-1] (320-space cols cbx+i0 .. cbx+i1) on the
;   current row (sy preset) in colour txt_col, via emit_span (LR x>>1). Preserves X.
.if 1
.proc emit_run
        lda t_cbx                    ; a = cbx + i0 + $8000  (bias to match emit_span)
        clc
        adc t_i0
        sta a_lo
        lda t_cbx+1
        adc #$80
        sta a_hi
        txa                          ; b = cbx + (X-1) + $8000
.if 1
        sbc #0                       ; C = 0 here (t_cbx hi <= 1: +$80 never carries), so
                                     ;   this is the -1; X >= 1 -> no borrow, C = 1
.else
        sec
        sbc #1
.endif
        clc
        adc t_cbx
        sta b_lo
        lda t_cbx+1
        adc #$80
        sta b_hi
        ; poly_color/scol are set ONCE per string in do_drawstring now (text colour is
        ; constant across the whole string), so emit_run no longer touches them per run.
        stx tmp_lo                   ; the span clobbers X (ldx sy) -> save it. On a
.if 1
er_go   jsr draw_scanline_fast.es_lrf ;  6502 a zp cell (3+3) beats txa/pha/pla/tax
.else                                 ;  (11); tmp_lo is free across the span path.
        jsr emit_span
.endif
        ldx tmp_lo
        rts
.endp
.else
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
.endif

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
LD_HOLD = 50                         ; vblanks the screen is held (~1 s PAL / 0.8 s NTSC)

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
        ; constant colour for the whole string (fill_span re-patches the mode fields
        ; when last_scol differs from LD_COL). No wait: only the START waits.
        lda vm_cur2
        sta BCB+BCB_DST_ADDR+2
        sta bcb_pg                   ; (the span BCB page / height shadow)
        lda #0
        sta BCB+BCB_HEIGHT
        sta bcb_ht
        lda #LD_COL
        sta poly_color
        sta scol
.if 1
        ldx #$D0                     ; the LOADING colour's mode fields go in once, like
        stx draw_scanline_fast.dsf_m ;   do_drawstring: every run then takes the fused
.ifdef HIRES_CAP                     ;   span path (LD_COL is solid, so bne / no copy)
        stx dsf_sr.dsr_m
        ldx hires
        lda er_tlo,x
        sta emit_run.er_go+1
        lda er_thi,x
        sta emit_run.er_go+2
.endif
        lda #LD_COL
        cmp last_scol
        beq ?lmok
        jsr span_mode
?lmok
.endif
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
        jmp ?char                   ; skill-ok JMPFLAG: a bcs here crosses a page (+1)
?done   jsr blit_idle                ; let the last glyph land before we show the page
        lda vm_cur2
        jsr show_page                ; re-assert the displayed page (now the LOADING screen)
        ; Hold it. Without this the screen lives exactly as long as the SIO read that
        ; follows, and that varies wildly: booted as a bare xex the read fails and
        ; retries (screen stays for seconds), off the real disk it succeeds so fast
        ; that the scene replaced it before it could be read at all. The ESC-into-game
        ; path was already legible at ~25 frames; the FIRST load (chained straight out
        ; of the intro) was not, so the hold is a full second now.
        ;   Counts VBI TICKS instead of waiting for RTCLOK3 to hit one exact value:
        ;   equality is a single-shot target, so a tick observed late leaves the loop
        ;   spinning a further ~5 s for the counter to wrap all the way round.
        ;   RTCLOK3 is bumped by the OS VBI, which is an NMI -- it keeps ticking under
        ;   load_part's sei. X is dead here (load_part reloads it from dk_idx).
        ; BOUNDED hold. The inner wait used to be `cmp RTCLOK3 / beq *-3` with no
        ; way out: if the OS VBI is not running (RTCLOK3 frozen) it spins FOREVER --
        ; with "LOADING..." already painted, which is indistinguishable from the
        ; loader hanging. ~130 k polls (~0.7 s) per tick, then give up the hold
        ; entirely and let the load start.
        ldx #LD_HOLD
        lda RTCLOK3
        sta ?last
?hold   lda #2
        sta ?hi
        ldy #0
?h2     lda ?last
        cmp RTCLOK3
        bne ?tick                    ; a jiffy went by -> count it
        dey
        bne ?h2
        dec ?hi
        bne ?h2
        rts                          ; RTCLOK3 dead -> do not hold at all
?tick   lda RTCLOK3
        sta ?last
        dex
        bne ?hold
        rts
?last   dta 0
?hi     dta 0
ld_str  dta c'LOADING...',0
.endp

    .ifdef LOAD_DEBUG
;=============================================================================
; ld_tint : A = 1..6, repaint palette-1 index 1 (the colour draw_loading printed
;   "LOADING..." in). load_part calls it between streams, so if the machine dies
;   mid-load the colour on screen says exactly WHICH stream it died in:
;       white  = never got past draw_loading / the very first SIO call
;       red    = video1     green = bytecode   blue = video2
;       yellow = palette    cyan  = sounds     white again = part fully loaded
;   Costs 6 register writes, touches nothing else, needs no font or blitter.
;=============================================================================
.proc ld_tint
        sec
        sbc #1
        sta ?t
        asl @
        clc
        adc ?t                       ; (stage-1)*3
        tax
        lda #1
        sta VBXE_PSEL                ; palette 1 (draw_loading's)
        lda #1
        sta VBXE_CSEL                ; index 1 = the text colour
        lda ?tab,x
        sta VBXE_CR
        lda ?tab+1,x
        sta VBXE_CG
        lda ?tab+2,x
        sta VBXE_CB
        rts
?t      dta 0
?tab    dta $F0,$00,$00              ; 1 red    - video1
        dta $00,$F0,$00              ; 2 green  - bytecode
        dta $40,$40,$F0              ; 3 blue   - video2
        dta $F0,$F0,$00              ; 4 yellow - palette
        dta $00,$F0,$F0              ; 5 cyan   - sounds
        dta $F0,$F0,$F0              ; 6 white  - done
.endp
    .endif

