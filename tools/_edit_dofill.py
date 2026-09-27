"""one-off source edit: game do_fill -- one zoom test per fill, the vertex loop ahead of the
clip dispatch, rs_z4 called directly on the zoomed path; do_hier -- the zoom test at entry
(inline bx/by for zoom 64) picks dh_sel."""
P = 'src_game/aw_polygon.asm'
s = open(P, encoding='latin-1').read()
a = s.index('        nocross do_fill, do_fill.df_ylo, do_fill.df_yno\n.proc do_fill\n')
b = s.index('.endp\n.else\n.proc do_fill', a)
old = s[a:b]
# the dispatch text, kept verbatim from the dispatch comment up to '?gnab'
d0 = old.index('        ; --- per-shape clip dispatch')
d1 = old.index('?gnab   ldy #0')
disp = old[d0:d1]
disp = disp.replace('        ;lda g_y0+1\n        bmi df_yno', 'df_disp lda g_y0+1\n        bmi df_yno')
disp = disp.replace('''        sty fill_poly_int.smc_dsi+2
        cmp #<draw_scanline_fast''', '''        sty fill_poly_int.smc_dsi+2
        ldy #0                      ; the decoder's Y = 0 invariant (miss path only)
        cmp #<draw_scanline_fast''')
assert 'df_disp lda g_y0+1' in disp and 'miss path only' in disp


def gen(rs, p):
    return f'''?{p}     jsr {rs}                    ; bbw  (A = scaled_lo, the read_scaled contract)
        sta bbw
        lda g_scaled_hi
        sta bbw+1
        jsr {rs}                    ; bbh
        sta bbh
        lda g_scaled_hi
        sta bbh+1
        m_pfetch                    ; n verts
        sta nverts
        lda bbw+1                   ; g_x0 = dr_x - bbw/2  (dr_x + ~half + 1)
        lsr @
        tax                         ; X = half hi ; C = bit 0 -> the lo ror
        lda bbw
        ror @
        eor #$FF
        sec
        adc dr_x
        sta g_x0
        txa
        eor #$FF
        adc dr_x+1
        sta g_x0+1
        lda bbh+1                   ; g_y0 = dr_y - bbh/2
        lsr @
        tax
        lda bbh
        ror @
        eor #$FF
        sec
        adc dr_y
        sta g_y0
        txa
        eor #$FF
        adc dr_y+1
        sta g_y0+1
        ldx #0                      ; X = vertex index, kept live across {rs}
?{p}v    jsr {rs}                    ; px = g_x0 + scaled
        clc
        adc g_x0
        sta pts_xlo,x
        lda g_x0+1
        adc g_scaled_hi
        sta pts_xhi,x
        jsr {rs}                    ; py = g_y0 + scaled
        clc
        adc g_y0
        sta pts_ylo,x
        lda g_y0+1
        adc g_scaled_hi
        sta pts_yhi,x
        inx
        cpx nverts
        bne ?{p}v
        jmp df_disp
'''


new = '''        nocross2 do_fill, do_fill.df_vy, do_fill.df_vye, do_fill.df_ylo, do_fill.df_yno
.proc do_fill
        lda rs_smc+1                ; ONE zoom test per fill: zoom 64 (rs_fast) reads the
        cmp #<rs_slow               ;   stream inline below; rs_z4 / rs_slow go out of
        jcs ?gen                    ;   line (lo bytes rs_fast < rs_slow < rs_z4, one page)
        m_pfetch                    ; bbw = byte, hi 0 (Y = 0, the decoder invariant)
        sta bbw
        sty bbw+1
        lsr @                       ; g_x0 = dr_x - bbw/2 (half hi = 0):
        eor #$FF                    ;   dr_x + ~half + 1, then dr_x+1 + $FF + C
        sec
        adc dr_x
        sta g_x0
        lda dr_x+1
        adc #$FF
        sta g_x0+1
        m_pfetch                    ; bbh
        sta bbh
        sty bbh+1
        lsr @                       ; g_y0 = dr_y - bbh/2
        eor #$FF
        sec
        adc dr_y
        sta g_y0
        lda dr_y+1
        adc #$FF
        sta g_y0+1
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
df_vy   lda (pb_ptr),y              ; px = g_x0 + byte
        iny
        clc
        adc g_x0
        sta pts_xlo,x
        lda g_x0+1
        adc #0
        sta pts_xhi,x
        lda (pb_ptr),y              ; py = g_y0 + byte
        iny
        clc
        adc g_y0
        sta pts_ylo,x
        lda g_y0+1
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
        jsr pf_wrap
''' + disp + '''?gnab   jmp fill_poly_int
?vlf    lda (pb_ptr),y              ; px = g_x0 + byte
        inc pb_ptr
        bne ?f1
        jsr pf_wrap                 ; (rare window cross, keeps A)
?f1     clc
        adc g_x0
        sta pts_xlo,x
        lda g_x0+1
        adc #0
        sta pts_xhi,x
        lda (pb_ptr),y              ; py = g_y0 + byte
        inc pb_ptr
        bne ?f2
        jsr pf_wrap
?f2     clc
        adc g_y0
        sta pts_ylo,x
        lda g_y0+1
        adc #0
        sta pts_yhi,x
        inx
        cpx nverts
        bne ?vlf
        jmp df_disp
?gen    bne ?gz                     ; C = 1 ; Z = rs_slow (zoom >= 16384, never in practice)
''' + gen('read_scaled', 'gs').replace('?gs     jsr', '        jsr', 1) + gen('rs_z4', 'gz') + '''        ert <rs_fast>=<rs_slow      ; the lo-byte order the entry test relies on
        ert <rs_slow>=<rs_z4
'''
s = s[:a] + new + s[b:]

# --- do_hier: the zoom test at entry; zoom 64 reads bx/by inline and keeps dh_sel = bit
h0 = s.index('.if 1\n.proc do_hier\n')
h1 = s.index('?loop\n', h0)
hnew = '''.if 1
.proc do_hier
        lda rs_smc+1                ; the zoom is the same for the whole tree: test it
        cmp #<rs_fast               ;   ONCE here -- zoom 64 reads bx/by inline and sets
        beq ?hf                     ;   dh_sel = bit (the child reads inline); any other
        jsr read_scaled             ;   zoom: read_scaled, dh_sel = jmp dh_cgen
        eor #$FF                    ; bx = dr_x - scaled  (A = scaled_lo:
        sec                         ;   dr_x + ~scaled + 1, carry-identical to sbc)
        adc dr_x
        sta bx
        lda dr_x+1
        sbc g_scaled_hi
        sta bx+1
        jsr read_scaled             ; by = dr_y - scaled
        eor #$FF
        sec
        adc dr_y
        sta by
        lda dr_y+1
        sbc g_scaled_hi
        sta by+1
        ldx #$4C                    ; jmp
?hsel   stx dh_sel
        m_pfetch                    ; child count (inlined poly_fetch, fps wave)
        sta hcount                  ; loop hcount+1 times
'''
s = s[:h0] + hnew + s[h1:]
old_h = '''?hdone  rts
dh_cgen                             ; zoom != 64: the generic child read, out of line
'''
assert old_h in s
s = s.replace(old_h, '''?hdone  rts
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
''', 1)
open(P, 'w', encoding='latin-1').write(s)
print('ok')
