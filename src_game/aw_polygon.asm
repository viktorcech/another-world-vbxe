;=============================================================================
; Playlist fetch : sequential read from VRAM via MEMAC-B.
;   pl_addr (24-bit) walks $060000.. ; bank = pl_addr>>14, win = $4000+addr&$3FFF.
;   Sets the bank every byte so it stays correct when interleaved with poly_byte.
;=============================================================================
; GAME fork (aw2 + aw3): the VM hammers pl_byte (3-4x per opcode). Mirror poly_fetch
; -- a running window pointer (pl_whi:pl_wlo) + a cached bank (pl_bank), recomputed
; only on a 16K crossing, with set_pl_ptr doing the heavy math once per jump. aw3 drops
; the per-byte pl_lo/pl_mid (the PC is DERIVED from the pointer on save, vm_save_pc), so
; operand fetches inline via mfetch -- skipping jsr/rts AND the per-fetch bank re-own.

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

; pl_byte : the OPCODE fetch -- re-owns the MEMAC-B bank (a draw may precede it), then
;   reads + advances. Operands use mfetch instead.
.proc pl_byte
        lda pl_bank                 ; re-own the bank if poly_fetch took it
        cmp memb_cur
        beq ?nosw
        sta memb_cur
        sta VBXE_MEMAC_B
?nosw   ldy #0
        lda (pl_wlo),y              ; A = the bytecode byte (return value)
        inc pl_wlo
        bne ?done
        jsr pl_wrap                 ; preserves A
?done   rts
.endp

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
        jsr poly_fetch              ;   generic (m*zoom)>>6 path, kept as fallback
        sta mul_m
        jmp mul_zoom                ; tail-call (mul_zoom keeps its own ==64 test)
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
?go     tax                         ; X = m (the table 'b' index)
        sec                         ; p2 = z4_hi * m  (16-bit, chained sbc)
fzh_1   lda fmul_sq1l,x             ; operand low bytes = 'a' (z4_hi), patched
fzh_2   sbc fmul_sq2l,x             ;   by rs_z4_set (fmulu convention)
        sta g_scaled_lo
fzh_3   lda fmul_sq1h,x
fzh_4   sbc fmul_sq2h,x
        sta g_scaled_hi
        sec                         ; p1 = z4_lo * m ; only hi(p1) is added, but
fzl_1   lda fmul_sq1l,x             ;   the hi sbc needs the lo borrow -> chain both
fzl_2   sbc fmul_sq2l,x
fzl_3   lda fmul_sq1h,x
fzl_4   sbc fmul_sq2h,x
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
; Edge slope  slope = (|dx| << 16) / dy , sign of dx applied (16.16, in N0..N3).
;   Reciprocal LUT + QS multiply instead of a 32/16 divide: |dx| is < 256 for
;   every edge in this intro (measured), so slope = |dx| * recip[dy], with
;   recip[dy] = round(65536/dy). dy==1 -> |dx|<<16 (recip 65536 won't fit 16b).
;   The 16-bit |dx|*recip is two QS 8x8 multiplies (was an 8x16 shift-add).
;   in : dv_lo:dv_hi (signed dx), hh (8-bit, dy>=1)
;=============================================================================
.proc calc_step
        lda dv_hi
        bpl ?pos
        sec                         ; dv = -dv  (|dx| now in dv_lo, dv_hi=0)
        lda #0
        sbc dv_lo
        sta dv_lo
        lda #0
        sbc dv_hi
        sta dv_hi
        lda #1
        sta dvsign
        jmp ?abs
?pos    lda #0
        sta dvsign
?abs    lda #0
        sta N0
        sta N1
        sta N2
        sta N3
        lda hh
        cmp #1
        bne ?mul
        lda dv_lo                   ; dy==1 : slope = |dx| << 16
        sta N2
        jmp ?sign
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
        sta N2                      ; N3 stays 0 (cleared at ?abs)
?sign   lda dvsign
        beq ?done
        lda N0                      ; negate slope (32-bit two's complement)
        eor #$FF
        clc
        adc #1
        sta N0
        lda N1
        eor #$FF
        adc #0
        sta N1
        lda N2
        eor #$FF
        adc #0
        sta N2
        lda N3
        eor #$FF
        adc #0
        sta N3
?done   rts
.endp

;=============================================================================
; Polygon decoder  (port of PolyData.draw / _fill / _hier)
;=============================================================================

; poly_draw : draw the shape at dr_off with dr_x,dr_y,dr_zoom,dr_col.
.proc poly_draw
        jsr poly_fetch              ; A = byte0 ; dr_off++
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
        jsr poly_fetch              ; n verts
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
        jmp ?gset
?yokx   ldx #<draw_scanline_yok     ; Y in range, X needs clipping -> skip the y-test only
        ldy #>draw_scanline_yok
        jmp ?gset
?yno    ldx #<draw_scanline         ; Y not fully on-screen -> full per-row y-test + X-clip
        ldy #>draw_scanline
?gset   stx fill_poly_int.smc_dsl+1
        sty fill_poly_int.smc_dsl+2
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
?gnab   lda #0
        sta g_vidx
?vl     jsr read_scaled             ; px = g_x0 + scaled
        lda g_x0
        clc
        adc g_scaled_lo
        pha
        lda g_x0+1
        adc g_scaled_hi
        tay
        ldx g_vidx
        pla
        sta pts_xlo,x
        tya
        sta pts_xhi,x
        jsr read_scaled             ; py = g_y0 + scaled
        lda g_y0
        clc
        adc g_scaled_lo
        pha
        lda g_y0+1
        adc g_scaled_hi
        tay
        ldx g_vidx
        pla
        sta pts_ylo,x
        tya
        sta pts_yhi,x
        inc g_vidx
        lda g_vidx
        cmp nverts
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
        jsr poly_fetch              ; child count
        sta hcount                  ; loop hcount+1 times
?loop
        jsr poly_fetch              ; word hi (big-endian)
        sta word_hi
        jsr poly_fetch              ; word lo
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
        jsr poly_fetch              ; ccol = poly[dr_off] & 0x7F
        and #$7F
        sta ccol
        jsr poly_fetch              ; (python off += 2 : skip the 2nd byte)
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
