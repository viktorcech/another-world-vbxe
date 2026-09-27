"""one-off source edit: intro fill_poly_int -- the game's "rare case out of line" pass
(draw_dots, the hy carries) plus the copy-mode key (the intro never draws copy mode)."""
p = 'src/aw_raster.asm'
s = open(p, encoding='latin-1').read()


def rp(o, n):
    global s
    assert s.count(o) == 1, o[:90]
    s = s.replace(o, n, 1)


a = s.index('''        bcs fpi_poly
        jmp draw_dots
.if 1
?mset   sta last_scol''')
b = s.index('''?poly
fpi_poly
''', a)
mset = s[a + len('        bcs fpi_poly\n        jmp draw_dots\n'):b]
s = s[:a] + '        bcc ?dots                   ; n < 3 (rare): draw_dots, out of line\n' + s[b:]
rp('''?hneg   inc i_idx                   ; h < 0 : nothing drawn and the edges untouched;''',
   '''?dots   jmp draw_dots
?mcpy   ldx #$F0                    ; copy mode is keyed $11 (never in the intro)
        lda #$11
        bne ?ma
''' + mset + '''?hneg   inc i_idx                   ; h < 0 : nothing drawn and the edges untouched;''')
rp('''        lda scol
        cmp #$11
        bcc ?ma
        ldx #$F0                    ; copy mode is keyed $11
        lda #$11
?ma     stx draw_scanline_fast.dsf_m''', '''        lda scol
        cmp #$11
        bcs ?mcpy                   ; copy mode (out of line)
?ma     stx draw_scanline_fast.dsf_m''')
rp('''        adc #2
        sta hy_lo
        bcc ?rh2
        inc hy_hi
?rh2    dec npair''', '''        adc #2
        sta hy_lo
        bcs ?rhc                    ; (rare: hy crosses a 256 boundary)
?rh2    dec npair''')
rp('''rh_end  bcc rh_top
?retal2 rts                         ; past the bottom -> whole shape done
''', '''rh_end  bcc rh_top
?retal2 rts                         ; past the bottom -> whole shape done
?rhc    inc hy_hi                   ; out of line: the hy += 2 carry (C stays 1)
        bcs ?rh2
''')
rp('''        jsr adv_edges1
        inc hy_lo
        bne ?rhl1
        inc hy_hi
?rhl1   jmp ?seg
''', '''        jsr adv_edges1
        inc hy_lo
        beq ?rhli                   ; (rare: hy crosses a 256 boundary)
        jmp ?seg
?rhli   inc hy_hi
        jmp ?seg
''')
open(p, 'w', encoding='latin-1').write(s)
print('ok')
