;=============================================================================
; op_drawtext (playlist 0x07) : DRAWTEXT strId(2) x(1) y(1) col(1)
;   Look strId up in the intro string table (aw_id_lo/hi), then render the
;   null-terminated bytes through the 8x8 font as ONE 4x8 BLT_BSTENCIL blit
;   per glyph: font_init pre-expands the 1bpp font into LR glyph cells in
;   VRAM (byte = $FF iff either of its 2 pixels is set, so the byte coverage
;   equals the old per-run floor(x/2) emit_span fills -- proven bit-identical
;   over the whole playlist by out/_txtval.py). (src $FF AND txt_col) = the
;   colour, BSTENCIL skips src==0 so the background under the glyph survives.
;   ~12 span blits/glyph collapsed into 1 (worst text frame ~6.4 -> ~0.4 vbl).
;   '\n' (0x0A) -> y+=8, x resets to the start column. Output mirrors
;   aw_text.draw_string / sim_atari.draw_text.
;=============================================================================

; Pre-expanded LR font in VRAM, right behind the control bank's XDL/BCB area:
; 96 glyphs x (8 rows x 4 bytes) = 3072 B. Same MEMAC-B bank as the control
; bank ($040000-$043FFF = bank $10); the blitter reads it by absolute address.
FONT_V      equ CTRL+$1000          ; $041000 (control bank uses $040000-$0401FF)
FONT_MBANK  equ FONT_V/$4000        ; $10 : MEMAC-B 16K bank of the font
FONT_W      equ DATAW+[FONT_V&$3FFF] ; $5000 : CPU window address (font_init)
FONT_MID    equ [FONT_V>>8]&$FF     ; $10 : src addr mid byte base (glyph index ORs in)

; the run-decomposition scratch died with the rewrite -> reuse two slots
t_gh    equ t_inrun                 ; current line: BCB HEIGHT (rows-1, clamped to row 199)
t_vis   equ t_i0                    ; current line: 1 = txt_y < 200 (line on screen)

op_drawtext
        jsr pl_byte
        sta t_sidlo                  ; strId lo  (NB: pl_byte clobbers tmp_lo)
        jsr pl_byte
        sta t_sidhi                  ; strId hi
        jsr pl_byte
        sta txt_x
        jsr pl_byte
        sta txt_y
        jsr pl_byte
        sta txt_col
        ; find table index for strId (linear scan; aw_nstr entries)
        ldx #0
?scan   cpx #aw_nstr
        bcc ?chk
        jmp ?done                    ; not found -> skip the string
?chk    lda aw_id_lo,x
        cmp t_sidlo
        bne ?nx
        lda aw_id_hi,x
        cmp t_sidhi
        beq ?found
?nx     inx
        bne ?scan
?found  lda aw_str_lo,x              ; txt_ptr = aw_strbytes + offset[x]
        clc
        adc #<aw_strbytes
        sta txt_ptr
        lda aw_str_hi,x
        adc #>aw_strbytes
        sta txt_ptr+1
        ; BCB constants for the WHOLE string (draw_glyph only patches SRC/DST
        ; addr + HEIGHT + fires): dst page, glyph cell geometry, colour mode.
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
        lda txt_col                  ; (src $FF AND col) XOR 0 = col; src $00 skipped
        sta BCB+BCB_AND
        lda #BLT_BSTENCIL
        sta BCB+BCB_CTRL
        lda #$FF                     ; text clobbers the span BCB mode fields ->
        sta last_scol                ;   force the next fill_span to re-patch them
        jsr line_clip                ; t_vis / t_gh for the start row
        lda txt_x
        sta t_cx
?char   ldy #0
        lda (txt_ptr),y
        bne ?notend
        jmp ?done                    ; 0x00 terminator
?notend inc txt_ptr                  ; ptr++ (16-bit)
        bne ?p1
        inc txt_ptr+1
?p1     cmp #$0A
        bne ?glyph
        lda txt_y                    ; newline : y += 8, cx = start x
        clc
        adc #8
        sta txt_y
        jsr line_clip
        lda txt_x
        sta t_cx
        jmp ?char
?glyph  sta t_ch
        lda t_vis
        beq ?adv                     ; line below the screen : don't draw
        lda t_cx
        cmp #40
        bcs ?adv                     ; column >= 40 : off the right, don't draw
        jsr draw_glyph
?adv    inc t_cx
        jmp ?char
?done   jsr blit_idle                ; restore SRC_STEPY = page stride: fill_span's
        lda #<SCRW                   ;   copy mode (scol>$10) never rewrites it and
        sta BCB+BCB_SRC_STEPY        ;   a 2-tall copy span would read row 2 from
        lda #>SCRW                   ;   src+4 instead of src+SCRW
        sta BCB+BCB_SRC_STEPY+1
        jmp next_op

; line_clip : txt_y -> t_vis (line on screen?) + t_gh (HEIGHT = rows-1 clamped
;   so the blit never writes past row 199 -- the old code clipped per row).
;   txt_y >= 200 hides the line (the old 8-bit y+j wrap at y >= 249 drew at the
;   top instead; no intro string reaches it -- max line y is 184, _txtval.py).
.proc line_clip
        lda txt_y
        cmp #SCRH
        bcs ?hid                     ; y >= 200 -> whole line hidden
        cmp #SCRH-7
        bcs ?clamp                   ; y >= 193 -> partial: height-1 = 199-y
        lda #7
        bne ?set                     ; (always)
?clamp  lda #SCRH-1
        sec
        sbc txt_y
?set    sta t_gh
        lda #1
        sta t_vis
        rts
?hid    lda #0
        sta t_vis
        rts
.endp

; draw_glyph : ONE stencil blit: VRAM font cell of t_ch -> column t_cx, row
;   txt_y, colour via BCB_AND (constants preset in op_drawtext). Fires without
;   waiting; the next BCB edit (here or in fill_span) is gated by its own
;   leading idle, like the polygon spans.
.proc draw_glyph
?bw     lda VBXE_BL_BUSY             ; inlined blit_idle: BCB edits below
        bne ?bw
        lda t_ch                     ; src = FONT_V + (ch-$20)*32
        sec
        sbc #$20
        tax
        lsr @
        lsr @
        lsr @                        ; idx>>3 = (idx*32) hi byte; idx<96 -> <$10,
        ora #FONT_MID                ;   so OR in the $10 base (no carry games)
        sta BCB+BCB_SRC_ADDR+1
        txa
        and #7
        tay
        lda m32tab,y                 ; (idx&7)*32 = (idx*32) lo byte
        sta BCB+BCB_SRC_ADDR
        lda t_cx                     ; dst = row_lut[txt_y] + ROWBIAS + cx*4
        asl @
        asl @                        ; cx <= 39 -> cx*4 <= 156, carry stays clear
        ldx txt_y
        adc row_lo,x                 ; row_lut is pre-biased by -ROWBIAS, add it
        sta BCB+BCB_DST_ADDR         ;   back (same bias path as emit_span's sx)
        lda row_hi,x
        adc #>ROWBIAS
        sta BCB+BCB_DST_ADDR+1
        lda t_gh
        sta BCB+BCB_HEIGHT
        lda #1                       ; fire, no wait (gated by the next leading idle)
        sta VBXE_BL_START
        rts
.endp
m32tab  dta 0,32,64,96,128,160,192,224

;=============================================================================
; font_init : one-time expansion of the 1bpp 8x8 font (aw_font at $B000, 768 B)
;   into the VRAM glyph cells at FONT_V: each font bit PAIR -> one LR byte,
;   $FF iff either pixel is set (== the old per-run floor(x/2) byte coverage).
;   Writes through the MEMAC-B window; call from start BEFORE snd_init (the
;   sound IRQ restores MEMAC_B to memb_cur and would yank the bank mid-write).
;   Leaves MEMAC_B = 0 (the setup_memac / memb_cur init state). Trashes the
;   text scratch (t_fp, txt_ptr, t_rbits) -- all free until the first 0x07 op.
;=============================================================================
.proc font_init
        lda #$80|FONT_MBANK
        sta VBXE_MEMAC_B             ; window $4000 -> $040000 (font cells at +$1000)
        lda #<aw_font
        sta t_fp
        lda #>aw_font
        sta t_fp+1
        lda #<FONT_W
        sta txt_ptr
        lda #>FONT_W
        sta txt_ptr+1
?src    ldy #0
        lda (t_fp),y
        sta t_rbits
?pair   lda #0                       ; out = $FF if bit 2j or 2j+1 set, else $00
        asl t_rbits
        bcc ?b2
        lda #$FF
?b2     asl t_rbits
        bcc ?wr
        lda #$FF
?wr     sta (txt_ptr),y
        iny
        cpy #4
        bne ?pair
        lda txt_ptr                  ; dst += 4
        clc
        adc #4
        sta txt_ptr
        bcc ?nc
        inc txt_ptr+1
?nc     inc t_fp                     ; src += 1
        bne ?ns
        inc t_fp+1
?ns     lda txt_ptr+1                ; 96*32 = 3072 B -> dst stops exactly at
        cmp #>[FONT_W+96*32]         ;   $5C00 (lo byte is 0 there; +4 steps)
        bne ?src
        lda #0                       ; MEMAC-B back off (memb_cur init state)
        sta VBXE_MEMAC_B
        rts
.endp
