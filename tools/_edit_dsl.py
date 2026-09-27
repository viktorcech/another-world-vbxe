"""one-off source edit: draw_scanline (game + intro) -- the profiled common order first.
The xl >= xr endpoint order is the one both builds take (the other never / 0.02 a
frame): it gets the single taken hop, the rare order the extra one. The clip tests
run not-taken on an on-screen span; the rare clips (a past the right edge, b past it,
a left of 0) sit out of line and come back. Same stores, same registers and flags at
the jmp for every path."""


def body(rdy_label):
    return '''        bcs dsl_xge                 ; xl >= xr: the common order (one short taken hop)
ds_xb   lda cr2                     ; xl < xr (rare): a=xl, b=xr
        sta b_lo
        lda cl2
        sta a_lo
        ldx cl3
        stx a_hi
        lda cr3
        sta b_hi
        bcc ?clip                   ; C = 0 (the bcs above not taken)
dsl_xge lda cl2                     ; xl >= xr : a=xr, b=xl
        sta b_lo
        lda cr2
        sta a_lo
        ldx cr3
        stx a_hi
        lda cl3
        sta b_hi
?clip   ; A = b_hi, X = a_hi through the tests (no reloads). Biased coords: real 0 =
        ; $8000, real 319 = $813F, all UNSIGNED. An on-screen span runs every test
        ; NOT taken; the rare clips sit out of line below and come back.
        cpx #$81
        bcs ?ahi                    ; a_hi >= $81 (rare): a may be past 319
?aok    cmp #$80                    ; if b < $8000 (b_real < 0) skip
        bcc ?ret
        cmp #$81
        bcs ?bhi                    ; b_hi >= $81 (rare): clip b to $813F
?bok    cpx #$80
        bcc ?alo                    ; a_hi < $80 (rare): clip a to $8000
''' + rdy_label + '''?ahi    bne ?ret                    ; a_hi > $81 -> a >= $8200 -> skip
        ldy a_lo
        cpy #$40
        bcc ?aok                    ; a <= $813F -> on
?ret    rts                         ; a >= $8140 (real >= 320) -> skip
?bhi    bne ?bmax                   ; b_hi > $81 -> clip
        ldy b_lo
        cpy #$40
        bcc ?bok                    ; b <= $813F -> ok
?bmax   lda #$3F
        sta b_lo
        lda #$81
        sta b_hi
        bne ?bok                    ; (A = $81)
?alo    lda #$00
        sta a_lo
        lda #$80
        sta a_hi
        bne ?ready                  ; (A = $80)
.endp
'''


# ---- game
P = 'src_game/aw_raster.asm'
s = open(P, encoding='latin-1').read()
a = s.index('        sbc cr3\n        bcc ?xll\n        lda cl2                     ; xl >= xr : a=xr, b=xl\n')
a += len('        sbc cr3\n')
b = s.index('?ret    rts\n.endp\n', a) + len('?ret    rts\n.endp\n')
s = s[:a] + body('''?ready
dsl_rdy jmp draw_scanline_fast.es_lrf ; operand PATCHED by set_render_mode: es_lrf
                                    ;   (LR) / dsf_sr.es_srf (SR), es_lr while baking
''') + s[b:]
o = '''; the endpoint ordering (bcc ?xll, taken for about half the clipped spans) must not
; straddle a page: pad in front of the proc when it would.
DSL_LEN = draw_scanline.dsl_xll+2-draw_scanline'''
assert o in s
s = s.replace(o, '''; the common endpoint order's hop (bcs dsl_xge, taken on every span) must not
; straddle a page: pad in front of the proc when it would.
DSL_LEN = draw_scanline.dsl_xge+1-draw_scanline''', 1)
open(P, 'w', encoding='latin-1').write(s)

# ---- intro
P = 'src/aw_raster.asm'
s = open(P, encoding='latin-1').read()
a = s.index('        sbc cr3\n        bcc ?xll\n.if 1\n        lda cr2                     ; xl >= xr')
a += len('        sbc cr3\n')
b = s.index('?ret    rts\n.endp\n', a) + len('?ret    rts\n.endp\n')
s = s[:a] + body('''ds_rdy
?ready  jmp draw_scanline_fast.es_lrf  ; the fused clipped span (text keeps emit_span)
''') + s[b:]
o = '        nocross dsl_r0, draw_scanline.ds_bok, draw_scanline.ds_rdy\n'
assert o in s
s = s.replace(o, '        nocross dsl_r0, draw_scanline.ds_xb, draw_scanline.dsl_xge   ; the common hop\n', 1)
open(P, 'w', encoding='latin-1').write(s)
print('ok')
