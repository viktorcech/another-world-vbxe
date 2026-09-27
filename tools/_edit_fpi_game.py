"""one-off source edit: game fill_poly_int -- 6502 skill "rare case out of line": the
profiled rare paths (draw_dots, the full-detail selector, the hy carry) leave the
common path, which now runs its branches not taken."""
p = 'src_game/aw_raster.asm'
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
''' + mset + '''?hneg   inc i_idx                   ; h < 0 : nothing drawn and the edges untouched;''')
rp('''        lda poly_bcb_h              ; 0 = full detail -> the classic 1x loop (placed
        jeq ?row                    ;   after this one); 1 = half detail -> PAIRED''',
   '''        lda poly_bcb_h              ; 0 = full detail -> the classic 1x loop (placed
        beq ?rowj                   ;   after this one); 1 = half detail -> PAIRED''')
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
?rowj   jmp ?row                    ; full detail (poly_bcb_h = 0)
''')
rp('''        jsr adv_edges1
        inc hy_lo
        bne ?rhl1
        inc hy_hi
?rhl1   lda #1
        sta rpar
        jmp ?seg
.if 1
        nocross fr_row''', '''        jsr adv_edges1
        inc hy_lo
        beq ?rhli                   ; (rare: hy crosses a 256 boundary)
?rhl1   lda #1
        sta rpar
        jmp ?seg
?rhli   inc hy_hi
        jmp ?rhl1
.if 1
        nocross fr_row''')
open(p, 'w', encoding='latin-1').write(s)
print('ok')
