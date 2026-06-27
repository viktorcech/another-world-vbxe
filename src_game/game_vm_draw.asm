;=============================================================================
; game_vm_draw.asm  --  decode a DRAW opcode and hand it to the polygon renderer.
;
;   Opcodes with the top bit(s) set are not table-dispatched -- they ARE the draw,
;   decoded straight from the opcode byte:
;     draw_bg     (op & 0x80) a full-screen background shape at zoom 1:1, video1.
;     draw_sprite (op & 0x40) the general case -- x/y from immediates or variables,
;                 an optional zoom, drawn from video1 or the shared video2 shapes.
;   Both unpack their operands into dr_off / dr_x / dr_y / dr_zoom, then fall into
;   do_draw, which picks the per-shape scaling path (self-modified) and calls the
;   shared poly_draw (aw_polygon.asm), going through the shape-cell cache when able.
;
;   Part of the game_vm split.
;=============================================================================

;=============================================================================
; draw_bg (op & 0x80) : off = ((op<<8 | b()) * 2) ; x,y = b(),b() ; zoom 64 ; video1
;=============================================================================
draw_bg
        sta vm_s2                   ; op = high byte of the offset word
        mfetch
        sta vm_s1                   ; low byte
        asl vm_s1                   ; off = word * 2
        rol vm_s2
        lda vm_s1
        sta dr_off
        lda vm_s2
        sta dr_off+1
        mfetch                 ; x = b()  (0..255)
        sta dr_x
        lda #0
        sta dr_x+1
        mfetch                 ; y = b()
        sta dr_y
        lda #0
        sta dr_y+1
        lda dr_y                    ; h = y - 199 ; if h>0 : y=199 ; x+=h
        sec
        sbc #199
        bcc ?noh
        beq ?noh
        sta vm_s1                   ; h (>0)
        lda #199
        sta dr_y
        lda dr_x
        clc
        adc vm_s1
        sta dr_x
        lda dr_x+1
        adc #0
        sta dr_x+1
?noh    lda #64                     ; zoom = 64
        sta dr_zoom
        lda #0
        sta dr_zoom+1
        lda #$FF
        sta dr_col
        lda #0
        sta poly_base_adj           ; video1
        sta psp
        jmp do_draw

;=============================================================================
; draw_sprite (op & 0x40) : the rawgl/game_sim operand decode, video1 or video2.
;=============================================================================
draw_sprite
        sta vm_op                   ; save opcode
        m_vm_w                    ; off = w() * 2
        asl vm_s1
        rol vm_s2
        lda vm_s1
        sta dr_off
        lda vm_s2
        sta dr_off+1
        mfetch                 ; x = b()
        sta dr_x
        lda #0
        sta dr_x+1
        lda vm_op
        and #$20
        bne ?xhi
        lda vm_op
        and #$10
        bne ?xvar
        lda dr_x                    ; x = (x<<8) | b()  (big-endian word)
        sta dr_x+1
        mfetch
        sta dr_x
        jmp ?xdone
?xvar   ldx dr_x                    ; x = var[x]
        lda var_lo,x
        sta dr_x
        lda var_hi,x
        sta dr_x+1
        jmp ?xdone
?xhi    lda vm_op                   ; op&0x20 : if op&0x10 -> x += 256
        and #$10
        beq ?xdone
        inc dr_x+1
?xdone  jsr pl_byte                 ; y = b()
        sta dr_y
        lda #0
        sta dr_y+1
        lda vm_op
        and #8
        bne ?ydone
        lda vm_op
        and #4
        bne ?yvar
        lda dr_y                    ; y = (y<<8) | b()
        sta dr_y+1
        mfetch
        sta dr_y
        jmp ?ydone
?yvar   ldx dr_y                    ; y = var[y]
        lda var_lo,x
        sta dr_y
        lda var_hi,x
        sta dr_y+1
?ydone  lda #64                     ; zoom = 64 ; default video1
        sta dr_zoom
        lda #0
        sta dr_zoom+1
        sta poly_base_adj
        lda vm_op
        and #2
        bne ?z2
        lda vm_op
        and #1
        beq ?zdone                  ; zoom stays 64
        mfetch                 ; zoom = var[b()]
        tax
        lda var_lo,x
        sta dr_zoom
        lda var_hi,x
        sta dr_zoom+1
        jmp ?zdone
?z2     lda vm_op
        and #1
        beq ?zbyte
        lda #8                      ; use video2 (shared shapes)
        sta poly_base_adj
        jmp ?zdone
?zbyte  jsr pl_byte                 ; zoom = b()
        sta dr_zoom
        lda #0
        sta dr_zoom+1
?zdone  lda #$FF
        sta dr_col
        lda #0
        sta psp
        ; fall through to do_draw

;=============================================================================
; do_draw : common BCB-per-shape setup + poly_draw  (mirrors op_drawpoly).
;=============================================================================
do_draw
        ; per-shape zoom dispatch : dr_zoom is constant through the whole
        ; poly_draw tree, so pick read_scaled's path ONCE here (SMC operand):
        ;   zoom == 64          -> rs_fast  (scaled = byte, no multiply)
        ;   zoom < 16384        -> rs_z4    (per-shape premultiply, 2 lookups/coord)
        ;   zoom >= 16384       -> rs_slow  (generic fallback; never in practice)
        ; (?ddz* labels: unique on purpose -- non-.proc ?-labels mis-bind, see
        ; the find_overflow.py lesson.)
        ldx #<rs_fast
        ldy #>rs_fast
        lda dr_zoom+1
        beq ?ddzlo
        cmp #$40
        bcs ?ddzg                   ; zoom >= 16384 -> generic
        bcc ?ddz4                   ; 256 <= zoom < 16384 -> z4
?ddzlo  lda dr_zoom
        cmp #64
        beq ?ddzk                   ; zoom == 64 -> rs_fast
?ddz4   jsr rs_z4_set               ; patch the z4 table operands for this shape
        ldx #<rs_z4
        ldy #>rs_z4
        bne ?ddzk                   ; (Y = >rs_z4 != 0 -> always taken)
?ddzg   ldx #<rs_slow
        ldy #>rs_slow
?ddzk   stx rs_smc+1
        sty rs_smc+2
        lda hires                   ; shape-cell cache: LR gameplay only (cells
        bne ?ddnc                   ;   are LR; SR 16008 uses the page uppers)
        jsr cc_draw                 ; C=1 -> drawn from the cache (hit or bake)
        bcs ?dddone
?ddnc   ; PERF (optimisation): set_poly_ptr moved AHEAD of blit_idle (old order idled
        ;   FIRST, then synced). set_poly_ptr is pure ZP + MEMAC-B window work (NOT the
        ;   BCB), so it now overlaps the previous shape's still-running blit -- the
        ;   blitter addresses via the BCB independently of the CPU window. ~50 cyc/shape
        ;   reclaimed; reads the same dr_off, so the rendered output is unchanged.
        jsr set_poly_ptr
        jsr blit_idle               ; only NOW wait, to edit the per-shape BCB fields
        lda cbase+2
        sta BCB+BCB_DST_ADDR+2
        lda poly_bcb_h              ; 0 = 1-tall spans (full) ; 1 = 2-tall (half-res, stock)
        sta BCB+BCB_HEIGHT
        jsr poly_draw
?dddone rts

