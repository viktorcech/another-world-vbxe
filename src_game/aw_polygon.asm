;=============================================================================
; Playlist fetch : sequential read from VRAM via MEMAC-B.
;   pl_addr (24-bit) walks $060000.. ; bank = pl_addr>>14, win = $4000+addr&$3FFF.
;   Sets the bank every byte so it stays correct when interleaved with poly_byte.
;=============================================================================
; GAME fork (aw2 + aw3): the VM hammers the bytecode fetch (3-4x per opcode). Mirror
; poly_fetch -- a running window pointer (pl_whi:pl_wlo) + a cached bank (pl_bank),
; recomputed only on a 16K crossing, with set_pl_ptr doing the heavy math once per
; jump. aw3 drops the per-byte pl_lo/pl_mid (the PC is DERIVED from the pointer on
; save, vm_save_pc), so operand fetches inline via mfetch -- skipping jsr/rts AND
; the per-fetch bank re-own. (2026-07-02 fps wave: the old `pl_byte` OPCODE-fetch
; routine is gone too -- its body, incl. the bank re-own, is inlined at vm_fetch
; in game_vm_sched.asm, and every mid-opcode caller now uses mfetch.)

; pl_wrap : handle the rare pointer wrap (256-byte page / 16K bank). Preserves A.
.proc pl_wrap
        pha
        inc pl_whi
        lda pl_whi
        cmp #$80                    ; past $7FFF -> next bank
        bne ?nc
        lda #>DATAW
        sta pl_whi
        inc pl_bank
        lda pl_bank
        sta memb_cur
        sta VBXE_MEMAC_B
?nc     pla
        rts
.endp

; mfetch : inline operand-byte fetch (A = byte, advance the pointer). Used WITHIN an
;   opcode, where no draw can have stolen the bank -> no bank re-own needed. (aw3)
.macro mfetch
        ldy #0
        lda (pl_wlo),y
        inc pl_wlo
        bne *+5                     ; no wrap (common) -> skip the 3-byte jsr pl_wrap
        jsr pl_wrap
.endm

; set_pl_ptr : sync pl_bank + the running window pointer (pl_whi:pl_wlo) from the
;   logical PC (pl_mid:pl_lo). Call on every PC JUMP (thread entry, jmp/call/ret/
;   djnz/condjmp) -- not per byte. bank = PLAY_BANK0 + (pc>>14), window = $4000 +
;   (pc & $3FFF); reuses the poly LUTs (offset to the PLAY_BANK0 base).
.proc set_pl_ptr
        ldx pl_mid
        lda poly_bank_lut,x         ; ((hi>>6)+POLY_BANK0)|$80
        clc
        adc #PLAY_BANK0-POLY_BANK0  ; shift base bank $14 -> $18 (bytecode region)
        sta pl_bank
        lda poly_win_lut,x          ; (hi&$3F)|>DATAW
        sta pl_whi
        lda pl_lo
        sta pl_wlo
        rts
.endp

;=============================================================================
; Poly data fetch  (from VRAM via MEMAC-B)
;=============================================================================

; set_poly_ptr : sync the running MEMAC-B stream pointer (poly_bnk + pb_ptr) from
;   dr_off.  Poly data is read SEQUENTIALLY, so the bank/window are computed only
;   here -- on a dr_off JUMP (do_draw start, do_hier child entry + restore) --
;   not per byte.  The MEMAC-B bank is set UNCONDITIONALLY here (memb_cur first:
;   the sound IRQ restores the register to memb_cur, so memb_cur must lead), which
;   lets poly_fetch drop its per-byte bank check: between set_poly_ptr calls only
;   poly_fetch touches the stream -- the VM fetches all draw operands (mfetch/
;   pl_byte) BEFORE do_draw, and load_part/load_bitmap run between draws.
;     bank = POLY_BANK0 + (off>>14) | $80 (+poly_base_adj) ; win = $4000+(off&$3FFF).
.proc set_poly_ptr
        ldx dr_off+1
        lda poly_bank_lut,x         ; ((hi>>6)+POLY_BANK0)|$80
        clc
        adc poly_base_adj           ; GAME fork: +0 video1, +8 video2 ($1C base).
        sta poly_bnk                ; do_hier re-syncs through set_poly_ptr, so the
        sta memb_cur                ; children of a video2 group stay in video2 banks.
        sta VBXE_MEMAC_B
        lda poly_win_lut,x          ; (hi&$3F)|>DATAW
        sta pb_ptr+1
        lda dr_off
        sta pb_ptr
        rts
.endp

; poly_fetch : A = next poly byte ; advances the running pb_ptr only.  No bank
;   check (set_poly_ptr guarantees the bank) and no dr_off upkeep (do_hier derives
;   it at save time via get_dr_off) -> the hot path is 20 cyc + jsr/rts. (Wave-2
;   port from the intro, see docs/SESSION-2026-06-10-intro-perf2.md.)
.proc poly_fetch
        ldy #0
        lda (pb_ptr),y
        inc pb_ptr
        beq pf_wrap                 ; ~1/256 : window page crossed
        rts
.endp

; m_pfetch : poly_fetch INLINED (fps wave) -- drops the 12-cyc jsr/rts on the hot
;   decode fetches (poly_draw byte0, do_fill nverts, do_hier's 4 per-child reads).
;   Byte-identical semantics to `jsr poly_fetch` (pf_wrap preserves A).
.macro m_pfetch
        ldy #0
        lda (pb_ptr),y
        inc pb_ptr
        bne *+5                     ; no wrap (common) -> skip the 3-byte jsr
        jsr pf_wrap
.endm

; pf_wrap : pb_ptr low wrapped (page cross). A = the just-read data byte, so the
;   window check preserves it with pha/pla -- paid only ~1/256 reads. Shared by
;   poly_fetch (fallthrough target) and rs_fast (jsr).
.proc pf_wrap
        pha
        inc pb_ptr+1
        lda pb_ptr+1
        cmp #$80                    ; crossed past $7FFF (16 KB window end)?
        bne ?nc
        lda #>DATAW                 ; reset window to $4000, advance to next bank
        sta pb_ptr+1
        inc poly_bnk
        lda poly_bnk
        sta memb_cur
        sta VBXE_MEMAC_B
?nc     pla
        rts
.endp

; get_dr_off : derive dr_off (the 16-bit poly offset) back from the stream
;   pointer:  dr_off = (poly_bnk - poly_base_adj - ($80|POLY_BANK0))<<14
;                      | (pb_ptr - DATAW).
;   Called only at the do_hier recursion save point. The video2 base shift
;   (poly_base_adj = 0 or 8) is subtracted back out, so the saved dr_off stays a
;   plain offset into the CURRENT bank group (matching what set_poly_ptr expects).
.proc get_dr_off
        lda poly_bnk
        sec
        sbc poly_base_adj           ; poly_bnk >= adj, so carry stays set
        sbc #$80+POLY_BANK0         ; bank delta 0..3 (one part < 64 KB per group)
        tax
        lda pb_ptr+1
        sec
        sbc #>DATAW                 ; window hi -> offset hi bits 8..13
        ora pf_bank_hi,x            ; | delta<<6
        sta dr_off+1
        lda pb_ptr
        sta dr_off
        rts
.endp
pf_bank_hi dta $00,$40,$80,$C0

;=============================================================================
; zoom scale :  scaled = (mul_m * dr_zoom) >> 6
;   FAST PATH: zoom == 64 (1:1, ~97% of all calls in the intro) -> (m*64)>>6 == m
;   exactly for m in 0..255, so skip the whole 8x16 multiply + >>6 shift.
;=============================================================================
.proc mul_zoom
        lda dr_zoom+1
        bne ?slow
        lda dr_zoom
        cmp #64
        bne ?slow
        lda mul_m                   ; scaled = m  (bit-identical to (m*64)>>6)
        sta g_scaled_lo
        lda #0
        sta g_scaled_hi
        rts
?slow   ; prod(24b) = mul_m * dr_zoom via two fmulu 8x8 (m set once), then >>6
        lda mul_m
        jsr fmul_seta               ; a = mul_m
        ldx dr_zoom                 ; b = zoom_lo
        jsr fmul_b
        lda qp_lo
        sta prod0
        lda qp_hi
        sta prod1
        ldx dr_zoom+1               ; b = zoom_hi
        jsr fmul_b
        lda prod1
        clc
        adc qp_lo
        sta prod1
        lda qp_hi
        adc #0
        sta prod2
        ; (prod2:prod1:prod0) >> 6  ==  high 16 bits of (prod << 2)  -- aw1.txt.
        ; GAME fork: shift the 24-bit product LEFT by 2 (prod0 -> A -> prod2 carry
        ; chain), then take A:prod2. Bit-identical to the 6-iteration >>6 loop
        ; (verified in tools/prof_mulzoom.py), ~90 cyc faster per slow-path call --
        ; a big win in the arene/final scenes (~3-42% of calls), 0 in water/cite.
        lda prod1
        asl prod0
        rol @
        rol prod2
        asl prod0
        rol @
        rol prod2
        sta g_scaled_lo
        lda prod2
        sta g_scaled_hi
        rts
.endp

; read_scaled : scaled = next poly byte * dr_zoom // 64.  The jmp operand is
;   PATCHED per shape by do_draw (dr_zoom is constant through the whole
;   poly_draw tree -- do_hier children inherit it): zoom==64 (1:1; backgrounds
;   and most sprites) -> rs_fast, which skips mul_zoom entirely ((m*64)>>6 == m);
;   anything else -> rs_slow (the full multiply path, aw1-optimised).
read_scaled
rs_smc  jmp rs_fast                 ; operand = rs_fast / rs_slow (SMC, per shape)

.proc rs_fast                       ; zoom == 64 : scaled = byte, exactly
        ldy #0
        lda (pb_ptr),y              ; inlined poly_fetch (saves the jsr/rts)
        inc pb_ptr
        beq ?w
        sta g_scaled_lo
        lda #0
        sta g_scaled_hi
        rts
?w      jsr pf_wrap                 ; rare window cross (preserves A)
        sta g_scaled_lo
        lda #0
        sta g_scaled_hi
        rts
.endp

.proc rs_slow                       ; zoom >= 16384 (never in practice) : the full
        stx ?xs                     ;   generic (m*zoom)>>6 path, kept as fallback.
        jsr poly_fetch              ;   X is saved/restored: the read_scaled contract
        sta mul_m                   ;   now guarantees X survives (do_fill ?vl keeps
        jsr mul_zoom                ;   the vertex index in X) and mul_zoom's fmul_b
        ldx ?xs                     ;   clobbers X.
        rts
?xs     dta 0
.endp

; rs_z4 : scaled = (m * dr_zoom) >> 6 for zoom < 16384, zoom != 64 -- the per-
;   SHAPE premultiply: rs_z4_set patched z4 = zoom<<2 into the square-table
;   operands below, so per coordinate this is just TWO table multiplies:
;     (m*zoom)>>6 == (m*z4)>>8 == m*z4_hi + hi(m*z4_lo)
;   (exact integer identity, proven exhaustively in Python incl. the 8-bit
;   table ops). ~85 cyc/coord vs ~200 for the old fmul_seta+2xfmul_b+>>6 chain
;   -- the dominant maths cost of the arene/final zoomed-sprite scenes.
.proc rs_z4
        ldy #0
        lda (pb_ptr),y              ; inlined poly_fetch
        inc pb_ptr
        beq ?w
?go     tay                         ; Y = m (the table 'b' index). Y, NOT X: the fps
        sec                         ;   wave keeps X = vertex index alive across
fzh_1   lda fmul_sq1l,y             ;   read_scaled in do_fill's ?vl loop, so every
fzh_2   sbc fmul_sq2l,y             ;   read_scaled path must PRESERVE X. abs,y costs
        sta g_scaled_lo             ;   the same as abs,x here. p2 = z4_hi * m.
fzh_3   lda fmul_sq1h,y
fzh_4   sbc fmul_sq2h,y
        sta g_scaled_hi
        sec                         ; p1 = z4_lo * m ; only hi(p1) is added, but
fzl_1   lda fmul_sq1l,y             ;   the hi sbc needs the lo borrow -> chain both
fzl_2   sbc fmul_sq2l,y
fzl_3   lda fmul_sq1h,y
fzl_4   sbc fmul_sq2h,y
        clc                         ; scaled = p2 + hi(p1)  (<= 65280, no overflow)
        adc g_scaled_lo
        sta g_scaled_lo
        bcc ?done
        inc g_scaled_hi
?done   rts
?w      jsr pf_wrap                 ; rare window cross (preserves A)
        jmp ?go
.endp

; rs_z4_set : prepare rs_z4 for this shape (called by do_draw when 64 != zoom
;   < 16384) -- z4 = dr_zoom<<2 (fits 16 bits), patch the 4+4 table operands
;   (a = z4_hi / z4_lo and their ^FF complements, the fmulu convention).
.proc rs_z4_set
        lda dr_zoom
        sta tmp_lo
        lda dr_zoom+1
        sta tmp_hi
        asl tmp_lo
        rol tmp_hi
        asl tmp_lo
        rol tmp_hi                  ; tmp = z4 = zoom<<2
        lda tmp_hi
        sta rs_z4.fzh_1+1
        sta rs_z4.fzh_3+1
        eor #$FF
        sta rs_z4.fzh_2+1
        sta rs_z4.fzh_4+1
        lda tmp_lo
        sta rs_z4.fzl_1+1
        sta rs_z4.fzl_3+1
        eor #$FF
        sta rs_z4.fzl_2+1
        sta rs_z4.fzl_4+1
        rts
.endp

;=============================================================================
; fmulu : unsigned 8x8 -> 16 square-table multiply (Fox/Tqa).  qp = a * b.
;   a*b = sq1[a+b] - sq2[(a^FF)+b],  sq1[i]=floor(i*i/4), sq2[j]=floor((255-j)**2/4).
;   The factors index the tables via the SELF-MODIFIED low byte of a page-aligned
;   base (a / a^FF) plus X (=b), so each table is 512 B (a+b reaches 510). ~14 cyc
;   for the lookup; split so the multiplicand 'a' is set once for repeated 'b'.
;   fmul_seta:  A = a   (patches the 4 table operands)
;   fmul_b:     X = b   -> qp_lo:qp_hi = a*b   (call after fmul_seta)   clobbers A
;=============================================================================
fmul_seta
        sta fmlb_l1+1              ; a -> low byte of the sq1 table operands
        sta fmlb_h1+1
        eor #$FF
        sta fmlb_l2+1             ; a^FF -> low byte of the sq2 table operands
        sta fmlb_h2+1
        rts

fmul_b
        sec
fmlb_l1 lda fmul_sq1l,x           ; sq1l[a+b]   (operand low byte patched to a)
fmlb_l2 sbc fmul_sq2l,x          ; sq2l[(a^FF)+b]
        sta qp_lo
fmlb_h1 lda fmul_sq1h,x
fmlb_h2 sbc fmul_sq2h,x
        sta qp_hi
        rts

;=============================================================================
; Edge slope  slope = (|dx| << 16) / dy , sign of dx applied (16.16).
;   Reciprocal LUT + QS multiply instead of a 32/16 divide: |dx| is < 256 for
;   every edge in this intro (measured), so slope = |dx| * recip[dy], with
;   recip[dy] = round(65536/dy). dy==1 -> |dx|<<16 (recip 65536 won't fit 16b).
;   The 16-bit |dx|*recip is two QS 8x8 multiplies (was an 8x16 shift-add).
;   in : dv_lo:dv_hi (signed dx), hh (8-bit, dy>=1)
;        X = 0 (right edge) or SMC_LD (left edge)
;   out: the 4 step bytes are written DIRECTLY into fill_poly_int's ?row SMC
;        adc-operands via abs,x (fps wave) -- the old N0..N3 staging + the
;        caller's 8-instruction copy block per edge (~28 cyc) are gone. The
;        negate is folded into the write-out (0 - N, borrow-chained), so the
;        old 4-byte eor/adc negate pass is gone too. Same values, same wrap.
;=============================================================================
.proc calc_step
        stx ?xsv                    ; the fmul calls below clobber X
        lda #0
        sta dvsign
        lda dv_hi
        bpl ?abs
        sec                         ; |dx| = -dv_lo (|dx| < 256 measured, so the
        lda #0                      ;   low byte is the whole magnitude -- the old
        sbc dv_lo                   ;   code negated dv_hi too but never read it)
        sta dv_lo
        lda #1
        sta dvsign
?abs    lda hh
        cmp #1
        bne ?mul
        lda #0                      ; dy==1 : slope = |dx| << 16 -> N = 0,0,|dx|
        sta N0
        sta N1
        lda dv_lo
        sta N2
        jmp ?wr
?mul    ; slope = |dx| * recip[hh], recip 16-bit -> two fmulu 8x8 multiplies:
        ;   N(24b) = (|dx|*recip_lo) + (|dx|*recip_hi << 8). |dx| is set once.
        lda dv_lo
        jsr fmul_seta               ; a = |dx|  (patched once for both mul-b)
        ldx hh
        lda recip_lo,x
        tax                         ; b = recip_lo
        jsr fmul_b                  ; p0 = |dx| * recip_lo
        lda qp_lo
        sta N0
        lda qp_hi
        sta N1
        ldx hh
        lda recip_hi,x
        tax                         ; b = recip_hi
        jsr fmul_b                  ; p1 = |dx| * recip_hi
        lda N1
        clc
        adc qp_lo
        sta N1
        lda qp_hi
        adc #0
        sta N2                      ; N3 is implicit 0 (byte 3 is emitted below)
?wr     ldx ?xsv                    ; write-out: straight into the ?row SMC adc
        lda dvsign                  ;   operands (X selects the cr / cl chain)
        bne ?neg
        lda N0
        sta fill_poly_int.smc_cr0+1,x
        lda N1
        sta fill_poly_int.smc_cr1+1,x
        lda N2
        sta fill_poly_int.smc_cr2+1,x
        lda #0
        sta fill_poly_int.smc_cr3+1,x
        beq ?dbl                    ; (A = 0 -> always taken)
?neg    sec                         ; step = 0 - N (32-bit, borrow-chained);
        lda #0                      ;   byte3 = 0-0-borrow = the sign extension
        sbc N0
        sta fill_poly_int.smc_cr0+1,x
        lda #0
        sbc N1
        sta fill_poly_int.smc_cr1+1,x
        lda #0
        sbc N2
        sta fill_poly_int.smc_cr2+1,x
        lda #0
        sbc #0
        sta fill_poly_int.smc_cr3+1,x
?dbl    lda poly_bcb_h              ; half mode (fps wave 2): the paired row loop
        beq ?ret                    ;   also needs step*2 in the smc2 chain. The
        lda fill_poly_int.smc_cr0+1,x   ; asl/rol pass over the 4 bytes just
        asl @                           ; written = (step << 1) mod 2^32, sign-
        sta fill_poly_int.smc2_cr0+1,x  ; agnostic (two's complement doubles the
        lda fill_poly_int.smc_cr1+1,x   ; same way). X still selects cr/cl.
        rol @
        sta fill_poly_int.smc2_cr1+1,x
        lda fill_poly_int.smc_cr2+1,x
        rol @
        sta fill_poly_int.smc2_cr2+1,x
        lda fill_poly_int.smc_cr3+1,x
        rol @
        sta fill_poly_int.smc2_cr3+1,x
?ret    rts
?xsv    dta 0
.endp

;=============================================================================
; Polygon decoder  (port of PolyData.draw / _fill / _hier)
;=============================================================================

; poly_draw : draw the shape at dr_off with dr_x,dr_y,dr_zoom,dr_col.
.proc poly_draw
        m_pfetch                    ; A = byte0 ; dr_off++  (inlined poly_fetch)
        cmp #$C0
        bcc ?notfill
        ; filled polygon : col = (dr_col&0x80) ? (byte0&0x3F) : dr_col
        pha
        lda dr_col
        and #$80
        beq ?usecol
        pla
        and #$3F
        jmp ?havecol
?usecol pla
        lda dr_col
?havecol
        sta fill_col
        jmp do_fill
?notfill
        and #$3F
        cmp #2
        bne ?ret
        jmp do_hier
?ret    rts
.endp

; do_fill : read bbox + vertices, build the point list, rasterise.
.proc do_fill
        jsr read_scaled             ; bbw
        lda g_scaled_lo
        sta bbw
        lda g_scaled_hi
        sta bbw+1
        jsr read_scaled             ; bbh
        lda g_scaled_lo
        sta bbh
        lda g_scaled_hi
        sta bbh+1
        m_pfetch                    ; n verts (inlined poly_fetch)
        sta nverts
        ; g_x0 = dr_x - bbw/2
        lda bbw+1
        lsr @
        sta tmp_hi
        lda bbw
        ror @
        sta tmp_lo
        lda dr_x
        sec
        sbc tmp_lo
        sta g_x0
        lda dr_x+1
        sbc tmp_hi
        sta g_x0+1
        ; g_y0 = dr_y - bbh/2
        lda bbh+1
        lsr @
        sta tmp_hi
        lda bbh
        ror @
        sta tmp_lo
        lda dr_y
        sec
        sbc tmp_lo
        sta g_y0
        lda dr_y+1
        sbc tmp_hi
        sta g_y0+1
        ; --- per-shape clip dispatch (SMC), 3-way -- Y and X tested INDEPENDENTLY so a
        ; shape that is fully on-screen VERTICALLY can skip the per-row y-test even when it
        ; still needs horizontal clipping:
        ;     Y in range AND X in range -> draw_scanline_fast (no y-test, no X-clip)
        ;     Y in range, X not         -> draw_scanline_yok  (no y-test, KEEP X-clip)  <- new
        ;     Y not in range            -> draw_scanline      (per-row y-test + X-clip)
        ; 1-px X margin (x0>=1, x1<=318) as before -- the recip-LUT edge walk can overshoot
        ; the hull by <1px. y is an exact integer row walk, so no Y margin is needed.
        ; --- Y in range?  y0 >= 0  AND  y1 = y0+bbh <= 199 ---
        lda g_y0+1
        bmi ?yno                    ; y0 < 0
        bne ?yno                    ; y0 >= 256 (y1 then can't be <= 199)
        lda g_y0
        clc
        adc bbh
        sta tmp_lo
        lda g_y0+1
        adc bbh+1
        bne ?yno                    ; y1 >= 256
        lda tmp_lo
        cmp #SCRH
        bcs ?yno                    ; y1 >= 200
        ; Y is fully on-screen. --- X in range?  x0 >= 1  AND  x1 = x0+bbw <= 318 ---
        lda g_x0+1
        bmi ?yokx                   ; x0 < 0 -> X-clip needed
        bne ?xinr                   ; x0 >= 256 -> left margin ok
        lda g_x0
        beq ?yokx                   ; x0 == 0 -> no left margin
?xinr   lda g_x0
        clc
        adc bbw
        sta tmp_lo
        lda g_x0+1
        adc bbw+1
        beq ?fast                   ; x1 <= 255 -> ok
        cmp #1
        bne ?yokx                   ; x1 >= 512
        lda tmp_lo
        cmp #$3F
        bcs ?yokx                   ; x1 >= 319 (margin wants <= 318)
?fast   ldx #<draw_scanline_fast    ; Y && X in range
        ldy #>draw_scanline_fast
        bne ?gyes                   ; (Y = >draw_scanline_fast != 0 -> always taken)
?yokx   ldx #<draw_scanline_yok     ; Y in range, X needs clipping -> skip the y-test only
        ldy #>draw_scanline_yok
?gyes   lda #<fill_poly_int.yk_row  ; Y fully on-screen -> ALSO skip the per-ROW
        sta fill_poly_int.smc_yj+1  ;   y-bounds test in the ?row loop (fps wave):
        lda #>fill_poly_int.yk_row  ;   hy provably stays in 0..199, so the ~11-cyc
        sta fill_poly_int.smc_yj+2  ;   test is dead weight on every scanline
        jmp ?gset
?yno    ldx #<draw_scanline         ; Y not fully on-screen -> full per-row y-test + X-clip
        ldy #>draw_scanline
        lda #<fill_poly_int.yk_tst  ; keep the per-row y-test (early exit past the
        sta fill_poly_int.smc_yj+1  ;   bottom edge / skip above the top)
        lda #>fill_poly_int.yk_tst
        sta fill_poly_int.smc_yj+2
?gset   stx fill_poly_int.smc_dsl+1
        sty fill_poly_int.smc_dsl+2
        stx fill_poly_int.smc_dsh+1 ; the half-mode paired loop has two more
        sty fill_poly_int.smc_dsh+2 ;   dispatched draw sites (pair + odd-exit
        stx fill_poly_int.smc_dsi+1 ;   row) -- patch them to the same target
        sty fill_poly_int.smc_dsi+2
        ; --- cell-cache bake guard: a fill on the CLIP dispatch may lose
        ; content silently (a child fully off-screen at the bake position
        ; emits NO spans -> the extents can't see it) -> the shape must not
        ; be cached. ~10 cyc per fill when not baking.
        cpx #<draw_scanline_fast
        bne ?gbk
        cpy #>draw_scanline_fast
        beq ?gnab                   ; fast dispatch = bbox fully on-screen, safe
?gbk    lda cc_baking
        beq ?gnab
        lda cc_flag
        ora #$80                    ; abort the bake -> NEVER
        sta cc_flag
?gnab   ldx #0                      ; X = vertex index, kept LIVE across read_scaled
                                    ;   (fps wave: rs_fast/rs_z4/rs_slow all preserve
                                    ;   X now) -- the old pha/tay/ldx/pla shuffle and
                                    ;   the g_vidx memory cell are gone (~35 cyc/vert)
?vl     jsr read_scaled             ; px = g_x0 + scaled
        lda g_x0
        clc
        adc g_scaled_lo
        sta pts_xlo,x
        lda g_x0+1
        adc g_scaled_hi
        sta pts_xhi,x
        jsr read_scaled             ; py = g_y0 + scaled
        lda g_y0
        clc
        adc g_scaled_lo
        sta pts_ylo,x
        lda g_y0+1
        adc g_scaled_hi
        sta pts_yhi,x
        inx
        cpx nverts
        bne ?vl
        jmp fill_poly_int
.endp

; do_hier : group node ; recurse over children.
.proc do_hier
        jsr read_scaled             ; bx = dr_x - scaled
        lda dr_x
        sec
        sbc g_scaled_lo
        sta bx
        lda dr_x+1
        sbc g_scaled_hi
        sta bx+1
        jsr read_scaled             ; by = dr_y - scaled
        lda dr_y
        sec
        sbc g_scaled_lo
        sta by
        lda dr_y+1
        sbc g_scaled_hi
        sta by+1
        m_pfetch                    ; child count (inlined poly_fetch, fps wave)
        sta hcount                  ; loop hcount+1 times
?loop
        m_pfetch                    ; word hi (big-endian)
        sta word_hi
        m_pfetch                    ; word lo
        sta word_lo
        jsr read_scaled             ; cx = bx + scaled
        lda bx
        clc
        adc g_scaled_lo
        sta cx
        lda bx+1
        adc g_scaled_hi
        sta cx+1
        jsr read_scaled             ; cy = by + scaled
        lda by
        clc
        adc g_scaled_lo
        sta cy
        lda by+1
        adc g_scaled_hi
        sta cy+1
        lda #$FF
        sta ccol
        lda word_hi
        bpl ?nocol                  ; bit15 clear -> no per-child colour
        m_pfetch                    ; ccol = poly[dr_off] & 0x7F
        and #$7F
        sta ccol
        m_pfetch                    ; (python off += 2 : skip the 2nd byte)
?nocol
        ; --- save _hier state, recurse, restore ---
        ; PERF (optimisation -- GAME FORK ONLY; the intro src/aw_polygon.asm still does the
        ;   old get_dr_off + set_poly_ptr round-trip, so the two forks diverge here):
        ;   cache the PARENT stream pointer directly (poly_bnk:pb_ptr) instead of deriving
        ;   dr_off via get_dr_off here and recomputing it via set_poly_ptr on restore.
        ;   poly_base_adj is constant across the whole shape tree, so the bank/window are
        ;   recoverable as-is -> restore is a plain copy, not a LUT recompute. Saves the
        ;   get_dr_off round-trip here AND the LUT recompute on restore (~70 cyc/hier-child).
        ;   Output is unchanged (get_dr_off <-> set_poly_ptr are inverses; the saved pointer
        ;   IS what the recompute would reproduce). NOTE: get_dr_off is now UNUSED in this build.
        ldx psp
        lda poly_bnk
        sta pstk,x
        inx
        lda pb_ptr
        sta pstk,x
        inx
        lda pb_ptr+1
        sta pstk,x
        inx
        lda bx
        sta pstk,x
        inx
        lda bx+1
        sta pstk,x
        inx
        lda by
        sta pstk,x
        inx
        lda by+1
        sta pstk,x
        inx
        lda hcount
        sta pstk,x
        inx
        lda dr_col
        sta pstk,x
        inx
        stx psp
        ; child draw params : dr_off = (word & 0x7FFF) * 2
        lda word_lo
        asl @
        sta dr_off
        lda word_hi
        and #$7F
        rol @
        sta dr_off+1
        lda cx
        sta dr_x
        lda cx+1
        sta dr_x+1
        lda cy
        sta dr_y
        lda cy+1
        sta dr_y+1
        lda ccol
        sta dr_col
        jsr set_poly_ptr            ; dr_off jumped to the child -> sync stream ptr
        jsr poly_draw
        ; restore
        ldx psp
        dex
        lda pstk,x
        sta dr_col
        dex
        lda pstk,x
        sta hcount
        dex
        lda pstk,x
        sta by+1
        dex
        lda pstk,x
        sta by
        dex
        lda pstk,x
        sta bx+1
        dex
        lda pstk,x
        sta bx
        dex
        lda pstk,x
        sta pb_ptr+1
        dex
        lda pstk,x
        sta pb_ptr
        dex
        lda pstk,x
        sta poly_bnk                ; parent stream pointer restored directly (no LUT)
        stx psp
        ; PERF (part of the same optimisation): re-own the MEMAC-B window for the parent
        ; bank DIRECTLY (was set_poly_ptr's LUT path). memb_cur LEADS (the sound IRQ
        ; restores the register to memb_cur); write the register ONLY when the child left
        ; us in a different bank -- shallow sibling groups share it, so this is usually
        ; skipped entirely. Net result identical to the old set_poly_ptr re-sync.
        lda poly_bnk
        cmp memb_cur
        beq ?samebk
        sta memb_cur
        sta VBXE_MEMAC_B
?samebk dec hcount
        bmi ?hdone                  ; childcount+1 iterations
        jmp ?loop
?hdone  rts
.endp
