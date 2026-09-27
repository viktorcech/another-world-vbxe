"""one-off source edit: the game's do_fill / do_hier passes ported to the intro
(src/aw_polygon.asm) -- one zoom test per fill, the vertex Y-walk ahead of the clip
dispatch, a memo'd dispatch, rs_z4 called directly; do_hier: the zoom test at entry
(inline bx/by for zoom 64, dh_sel), the parent stream pointer cached on pstk with
set_poly_ptr inline, m_pfetch for the colour bytes."""
P = 'src/aw_polygon.asm'
s = open(P, encoding='latin-1').read()


def cut(start, end_marker):
    a = s.index(start)
    b = s.index(end_marker, a)
    return a, b


# ---- rs_slow keeps X (the vertex loop keeps its index in X across the reads)
old_slow = '''.proc rs_slow                       ; zoom != 64 : full (m*zoom)>>6
        jsr poly_fetch
        sta mul_m
        jmp mul_zoom                ; tail-call (mul_zoom keeps its own ==64 test)
.endp'''
assert old_slow in s
s = s.replace(old_slow, '''.proc rs_slow                       ; zoom >= 16384 (never in practice): full (m*zoom)>>6
        stx ?xs                     ; X is kept: do_fill's vertex loop holds its index
        jsr poly_fetch              ;   there across the reads (mul_zoom's fmul_b uses X)
        sta mul_m
        jsr mul_zoom                ; A = scaled_lo (read_scaled contract)
        ldx ?xs
        rts
?xs     dta 0
.endp''', 1)


def gen(rs, p):
    return f'''?{p}     jsr {rs}                    ; bbw  (A = scaled_lo, the read_scaled contract)
        sta bbw
        lda scaled_hi
        sta bbw+1
?{p}h    jsr {rs}                    ; bbh
        sta bbh
        lda scaled_hi
        sta bbh+1
        m_pfetch                    ; n verts
        sta nverts
        lda bbw+1                   ; x0 = dr_x - bbw/2  (dr_x + ~half + 1)
        lsr @
        tax                         ; X = half hi ; C = bit 0 -> the lo ror
        lda bbw
        ror @
        eor #$FF
        sec
        adc dr_x
        sta x0
        txa
        eor #$FF
        adc dr_x+1
        sta x0+1
        lda bbh+1                   ; y0 = dr_y - bbh/2
        lsr @
        tax
        lda bbh
        ror @
        eor #$FF
        sec
        adc dr_y
        sta y0
        txa
        eor #$FF
        adc dr_y+1
        sta y0+1
        ldx #0                      ; X = vertex index, kept live across the reads
?{p}v    jsr {rs}                    ; px = x0 + scaled
        clc
        adc x0
        sta pts_xlo,x
        lda x0+1
        adc scaled_hi
        sta pts_xhi,x
?{p}y    jsr {rs}                    ; py = y0 + scaled
        clc
        adc y0
        sta pts_ylo,x
        lda y0+1
        adc scaled_hi
        sta pts_yhi,x
        inx
        cpx nverts
        bne ?{p}v
        jmp df_disp
'''


new_fill = '''.if 1
        nocross2 do_fill, do_fill.df_vy, do_fill.df_vye, do_fill.df_disp, do_fill.df_dse
.proc do_fill
        lda rs_smc+1                ; ONE zoom test per fill: zoom 64 (rs_fast, ~97% of
        cmp #<rs_slow               ;   the intro) reads the stream inline below; rs_z4 /
        jcs ?gen                    ;   rs_slow out of line (lo bytes fast < slow < z4)
        m_pfetch                    ; bbw = byte, hi 0 (Y = 0, the decoder invariant)
        sta bbw
        sty bbw+1
        lsr @                       ; x0 = dr_x - bbw/2 (half hi = 0):
        eor #$FF                    ;   dr_x + ~half + 1, then dr_x+1 + $FF + C
        sec
        adc dr_x
        sta x0
        lda dr_x+1
        adc #$FF
        sta x0+1
        m_pfetch                    ; bbh
        sta bbh
        sty bbh+1
        lsr @                       ; y0 = dr_y - bbh/2
        eor #$FF
        sec
        adc dr_y
        sta y0
        lda dr_y+1
        adc #$FF
        sta y0+1
        m_pfetch                    ; n verts
        sta nverts
        ldx #0                      ; X = vertex index
        asl @                       ; the 2n vertex bytes are read (pb),y and the pointer
        bcs ?vlf                    ;   moves once after -- unless they may run past the
        adc pb_ptr                  ;   window end ($7FFF), or n >= 128 (Y would wrap).
        bcc df_vy                   ;   C = 0 here.
        lda pb_ptr+1
        cmp #$7F
        beq ?vlf                    ; last window page + a page cross: byte by byte
df_vy   lda (pb_ptr),y              ; px = x0 + byte
        iny
        clc
        adc x0
        sta pts_xlo,x
        lda x0+1
        adc #0
        sta pts_xhi,x
        lda (pb_ptr),y              ; py = y0 + byte
        iny
        clc
        adc y0
        sta pts_ylo,x
        lda y0+1
        adc #0
        sta pts_yhi,x
        inx
        cpx nverts
        bne df_vy
df_vye  tya                         ; pb_ptr += 2n, the window wrap as the byte
        clc                         ;   fetch does it (pf_wrap on a low-byte wrap)
        adc pb_ptr
        sta pb_ptr
        ldy #0                      ; the decoder's Y = 0 invariant
        bcc df_disp
        jsr pf_wrap                 ; (rare window cross)
        jmp df_disp
?vlf    lda (pb_ptr),y              ; px = x0 + byte
        inc pb_ptr
        bne ?f1
        jsr pf_wrap                 ; (rare window cross, keeps A)
?f1     clc
        adc x0
        sta pts_xlo,x
        lda x0+1
        adc #0
        sta pts_xhi,x
        lda (pb_ptr),y              ; py = y0 + byte
        inc pb_ptr
        bne ?f2
        jsr pf_wrap
?f2     clc
        adc y0
        sta pts_ylo,x
        lda y0+1
        adc #0
        sta pts_yhi,x
        inx
        cpx nverts
        bne ?vlf
        ; --- per-shape clip dispatch (SMC) : bbox fully on-screen (75% of intro
        ; fills; vertices never leave the bbox -- measured over the whole intro) ->
        ; the raster's three draw sites go to draw_scanline_fast (no per-row y test /
        ; X clip); anything else -> the full clip path. The sites keep the last
        ; target (usually the same): unchanged -> no stores. X holds x1/y1 lo, so the
        ; decoder's Y = 0 survives.
df_disp lda x0+1
        ora y0+1
        bmi df_clip                 ; x0 < 0 or y0 < 0
        lda x0                      ; x1 = x0 + bbw  must be <= 319
        clc
        adc bbw
        tax
        lda x0+1
        adc bbw+1
        beq ?xok                    ; x1 <= 255 -> ok
        cmp #1
        bne df_clip                 ; x1 >= 512
        cpx #$40
        bcs df_clip                 ; x1 >= 320
?xok    lda y0                      ; y1 = y0 + bbh  must be <= 199
        clc
        adc bbh
        tax
        lda y0+1
        adc bbh+1
        bne df_clip
        cpx #SCRH
        bcs df_clip                 ; y1 >= 200
        lda #<draw_scanline_fast
        cmp fill_poly_int.smc_dsl+1
        beq df_dse                  ; unchanged -> no stores
        ldx #>draw_scanline_fast
        bne ?set                    ; (X != 0: always)
df_clip lda #<draw_scanline
        cmp fill_poly_int.smc_dsl+1
        beq df_dse
        ldx #>draw_scanline
?set    sta fill_poly_int.smc_dsl+1
        stx fill_poly_int.smc_dsl+2
        sta fill_poly_int.smc_dsh+1 ; the paired loop has two more dispatched draw
        stx fill_poly_int.smc_dsh+2 ;   sites (pair row + odd-exit row)
        sta fill_poly_int.smc_dsi+1
        stx fill_poly_int.smc_dsi+2
df_dse  jmp fill_poly_int
        ert <draw_scanline_fast=<draw_scanline   ; the low byte alone tells them apart
?gen    bne ?gz                     ; C = 1 ; Z = rs_slow (zoom >= 16384, never in
        lda #<rs_slow               ;   practice): run the rs_z4 copy below with its four
        jsr ?gpat                   ;   reads pointed at rs_slow, then point them back
        jsr ?gz                     ;   (?gz ends in jmp fill_poly_int -> its rts lands here)
        lda #<rs_z4
?gpat   sta ?gz+1
        sta ?gzh+1
        sta ?gzv+1
        sta ?gzy+1
        rts
''' + gen('rs_z4', 'gz') + '''        ert <rs_fast>=<rs_slow      ; the lo-byte order the entry test relies on
        ert <rs_slow>=<rs_z4
        ert [>rs_fast]<>[>rs_z4]    ; one page: the lo byte alone tells them apart
        ert [>rs_slow]<>[>rs_z4]    ;   (and ?gpat patches the lo bytes only)
.endp
.else
'''
a = s.index('.if 1\n.proc do_fill\n')
b = s.index('.else\n.proc do_fill\n', a)
s = s[:a] + new_fill + s[b + len('.else\n'):]

# ---- do_hier
new_hier = '''.if 1
        nocross do_hier, do_hier.dh_wh, do_hier.dh_whe
.proc do_hier
        lda rs_smc+1                ; the zoom is the same for the whole tree: test it
        cmp #<rs_fast               ;   ONCE here -- zoom 64 reads bx/by inline and sets
        jeq ?hf                     ;   dh_sel = bit (the child reads inline); any other
        jsr read_scaled             ;   zoom: read_scaled, dh_sel = jmp dh_cgen
        eor #$FF                    ; bx = dr_x - scaled  (A = scaled_lo:
        sec                         ;   dr_x + ~scaled + 1, carry-identical to sbc)
        adc dr_x
        sta bx
        lda dr_x+1
        sbc scaled_hi
        sta bx+1
        jsr read_scaled             ; by = dr_y - scaled
        eor #$FF
        sec
        adc dr_y
        sta by
        lda dr_y+1
        sbc scaled_hi
        sta by+1
        ldx #$4C                    ; jmp
?hsel   stx dh_sel
        m_pfetch                    ; child count (inlined fetch)
        sta hcount                  ; loop hcount+1 times
?loop
dh_wh
        m_pfetch                    ; word hi (big-endian)
dh_whe  sta word_hi
        m_pfetch                    ; word lo
        sta word_lo
        ; the child position is composed STRAIGHT into dr_x/dr_y (nothing between
        ; here and the child draw reads them; bx/by were derived already).
dh_sel  bit dh_cgen                 ; SMC opcode (set above): bit = zoom 64, the reads
                                    ;   below inline; jmp = the generic path (out of line)
        lda (pb_ptr),y              ; dr_x = cx = bx + byte  (Y = 0)
        inc pb_ptr
        bne ?c1
        jsr pf_wrap
?c1     clc
        adc bx
        sta dr_x
        lda bx+1
        adc #0
        sta dr_x+1
        lda (pb_ptr),y              ; dr_y = cy = by + byte
        inc pb_ptr
        bne ?c2
        jsr pf_wrap
?c2     clc
        adc by
        sta dr_y
        lda by+1
        adc #0
        sta dr_y+1
?cdone  lda #$FF
        sta ccol
        lda word_hi
        bpl ?nocol                  ; bit15 clear -> no per-child colour
        m_pfetch                    ; ccol = poly[dr_off] & 0x7F
        and #$7F
        sta ccol
        m_pfetch                    ; (python off += 2 : skip the 2nd byte)
?nocol
        ; --- save _hier state, recurse, restore --- the PARENT stream pointer
        ; (poly_bnk:pb_ptr) is cached on pstk instead of get_dr_off here + set_poly_ptr
        ; on restore (the game fork's pass): poly_base_adj-free intro banks, so the
        ; saved pointer IS what the recompute would reproduce. 9-byte frame at pstk+psp.
        ldx psp
        lda poly_bnk
        sta pstk,x
        lda pb_ptr
        sta pstk+1,x
        lda pb_ptr+1
        sta pstk+2,x
        lda bx
        sta pstk+3,x
        lda bx+1
        sta pstk+4,x
        lda by
        sta pstk+5,x
        lda by+1
        sta pstk+6,x
        lda hcount
        sta pstk+7,x
        lda dr_col
        sta pstk+8,x
        txa
        clc
        adc #9
        sta psp
        ; child draw params : dr_off = (word & 0x7FFF) * 2, set_poly_ptr inline
        lda word_lo
        asl @
        sta dr_off
        sta pb_ptr                  ; (the window low byte)
        lda word_hi
        and #$7F
        rol @
        sta dr_off+1
        tax
        lda poly_bank_lut,x         ; ((hi>>6)+POLY_BANK0)|$80
        sta poly_bnk
        sta memb_cur
        sta VBXE_MEMAC_B
        lda poly_win_lut,x          ; (hi&$3F)|>DATAW
        sta pb_ptr+1
        lda ccol                    ; (dr_x/dr_y already hold cx/cy, see above)
        sta dr_col
        jsr poly_draw
        ldy #0                      ; the child's raster clobbered Y -> restore the
        lda psp                     ;   decoder's Y = 0 invariant for the fetches below
        sec
        sbc #9
        sta psp
        tax
        lda pstk+8,x
        sta dr_col
        lda pstk+7,x
        sta hcount
        lda pstk+6,x
        sta by+1
        lda pstk+5,x
        sta by
        lda pstk+4,x
        sta bx+1
        lda pstk+3,x
        sta bx
        lda pstk+2,x
        sta pb_ptr+1
        lda pstk+1,x
        sta pb_ptr
        lda pstk,x
        sta poly_bnk                ; parent stream pointer restored directly
        cmp memb_cur                ; re-own MEMAC-B only when the child left another
        beq ?samebk                 ;   bank (memb_cur leads: the sound IRQ restores
        sta memb_cur                ;   the register to it)
        sta VBXE_MEMAC_B
?samebk dec hcount
        bmi ?hdone                  ; childcount+1 iterations
        jmp ?loop
?hdone  lda psp                     ; the outermost group leaves dr_off = its last
        beq ?dro                    ;   save point, as the get_dr_off walk did
        rts
?dro    jmp get_dr_off
?hf     m_pfetch                    ; zoom 64: bx = dr_x - byte (hi 0)
        eor #$FF
        sec
        adc dr_x
        sta bx
        lda dr_x+1
        sbc #0
        sta bx+1
        m_pfetch                    ; by = dr_y - byte
        eor #$FF
        sec
        adc dr_y
        sta by
        lda dr_y+1
        sbc #0
        sta by+1
        ldx #$2C                    ; bit
        jmp ?hsel
dh_cgen                             ; zoom != 64: the generic child read, out of line
        jsr read_scaled             ; dr_x = cx = bx + scaled  (A = scaled_lo)
        clc
        adc bx
        sta dr_x
        lda bx+1
        adc scaled_hi
        sta dr_x+1
        jsr read_scaled             ; dr_y = cy = by + scaled
        clc
        adc by
        sta dr_y
        lda by+1
        adc scaled_hi
        sta dr_y+1
        jmp ?cdone
.endp
.else
'''
a = s.index('.if 1\n        nocross do_hier, do_hier.dh_wh, do_hier.dh_whe\n.proc do_hier\n')
b = s.index('.else\n.proc do_hier\n', a)
s = s[:a] + new_hier + s[b + len('.else\n'):]
slow_new = s[s.index('.proc rs_slow '):]
slow_new = slow_new[:slow_new.index('.endp') + 6]
s = s.replace(slow_new, '', 1)
anchor = '''        rts
.endp

.if 1
; rs_z4 : the per-SHAPE premultiply'''
assert anchor in s
s = s.replace(anchor, '''        rts
.endp

''' + slow_new + '''
.if 1
; rs_z4 : the per-SHAPE premultiply''', 1)
anchor2 = '''.if 1
; read_scaled CONTRACT'''
assert anchor2 in s
s = s.replace(anchor2, '''.if 1
        nocross read_scaled, rs_fast, rs_z4   ; the three entries share one page (do_fill
                                    ;   tells them apart by the operand's lo byte)
; read_scaled CONTRACT''', 1)
open(P, 'w', encoding='latin-1').write(s)
print('ok')
