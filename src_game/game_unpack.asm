;=============================================================================
; game_unpack.asm  -  ZX02 stream decoder for the packed part data on the disk.
;
;   tools/make_game_atr.py packs every resource as 16 KB bank chunks, each chunk
;   its OWN ZX02 stream (so back-references stay inside the MEMAC-B window), the
;   streams of one resource back to back, padded to a sector pair. The decoder is
;   DMSC's zx02-optim (MIT) with the input pulled off the disk and the match copy Y-indexed: zx_src walks the
;   256-byte PKBUF and every wrap refills it with the next two sectors. SIO is the
;   bottleneck (~180 ms a pair vs ~17 ms to decode it), not this loop.
;   make_game_atr.zx02_decode is a bit-exact model of this code: every stream on
;   the disk is round-tripped through it at build time.
;=============================================================================

PKBUF   = $0700                     ; 256 B = two sectors. The boot loader's page:
                                    ;   dead once it has jumped into the game.
; zero page: the raster accumulators cr0..cl3 + hy_lo. fill_poly_int / op_drawstring
; reinitialise them on entry and a part load never runs inside a draw.
zx_off  = $C0                       ; (2) match offset - 1
zx_src  = $C2                       ; (2) packed byte ptr, high byte stays >PKBUF
zx_dst  = $C4                       ; (2) output ptr
zx_bitr = $C6                       ; bit reservoir, sentinel-terminated
zx_ptr  = $C7                       ; (2) match source ptr

;-----------------------------------------------------------------------------
; pk_open : start reading the packed resource at sector dk_sec (fills PKBUF).
;-----------------------------------------------------------------------------
pk_open
        lda dk_sec
        sta DAUX1
        lda dk_sec+1
        sta DAUX2
        lda #>PKBUF
        sta zx_src+1
        ; fall into pk_fill (its zx_src store is the open's; on a refill it is 0 already)

;-----------------------------------------------------------------------------
; pk_fill : read the next two sectors (DAUX walks on) into PKBUF. A sector that
;   fails every retry is re-asked forever: a garbled stream would write past the
;   window, so a stalled loader (red border) beats a wrecked machine.
;   Out: Y = SIOV status, A/X clobbered.
;-----------------------------------------------------------------------------
pk_fill
        lda #<PKBUF
        sta zx_src
        sta DBUFLO
        lda #>PKBUF
        sta DBUFHI
pk_rd   ldx #1
        jsr read_sectors            ; C=1: DBUF/DAUX untouched -> the same sector again
        bcs pk_bad
        lda DBUFLO                  ; $80 after the first sector, $00 after the second
        bmi pk_rd
        rts
pk_bad  lda #$34
        sta $D01A                   ; COLBK
        bne pk_rd

;-----------------------------------------------------------------------------
; pk_refill : zx_src wrapped to PKBUF+0 -> refill. Keeps A, X and C (the decoder
;   has a live byte, count or ror bit in them) and returns Y = 0.
;-----------------------------------------------------------------------------
pk_refill
        php
        pha
        stx pk_x
        jsr pk_fill
        ldx pk_x
        ldy #0
        pla
        plp
        rts
pk_x    dta 0

;-----------------------------------------------------------------------------
; pk_bank : decode the next stream into VRAM bank A (bare number) at DATAW.
; pk_dec  : decode the next stream to zx_dst.
;   Out: Y = 0, C = 0 (both exits reach zx_end from zx_elias, which returns C=0).
;-----------------------------------------------------------------------------
pk_bank
        ora #$80
        sta VBXE_MEMAC_B
        sta memb_cur                ; keep the poly/pl_byte bank cache consistent
        lda #<DATAW
        sta zx_dst
        lda #>DATAW
        sta zx_dst+1
        ; fall into pk_dec
pk_dec
        ldy #0
        sty zx_off                  ; initial offset 1 (zx02's default -o 1)
        sty zx_off+1
        lda #$80
        sta zx_bitr

; literal run: Elias(length) byte[1] .. byte[length]
zx_lit  ldx #1
        jsr zx_elias                ; X = A = length, Z = (256), C = 0
        beq zx_lsl                  ; 256 literals (rare): byte loop
        adc zx_src                  ; C=0: does the run stay inside PKBUF?
        bcs zx_lsl                  ; reaches its end (1 run in ~256 B): byte loop
zx_lf   lda (zx_src),y              ; Y walks, pointers move once after the run
        sta (zx_dst),y
        iny
        dex
        bne zx_lf
        tya                         ; src += length: C=0 (the bcs above), < 256
        adc zx_src
        sta zx_src
        tya                         ; dst += length: C=0 (the sum above)
        adc zx_dst
        sta zx_dst
        bcs zx_lh                   ; next output page (rare), out of line
zx_l0   ldy #0
zx_l1   asl zx_bitr
        bcs zx_new
; repeat the last offset: Elias(length)
        inx
        jsr zx_elias
zx_copy lda zx_dst                  ; ptr = dst - (offset-1) - 1  (C=0 from zx_elias)
        sbc zx_off
        sta zx_ptr
        lda zx_dst+1
        sbc zx_off+1
        sta zx_ptr+1
zx_cp1  lda (zx_ptr),y              ; Y walks, pointers move once after the run:
        sta (zx_dst),y              ;   ptr < dst, so an overlapping run still
        iny                         ;   reads every byte after it was written
        dex
        bne zx_cp1
        dey                         ; dst += length: C=1 (the ptr subtract never
        tya                         ;   borrows), so add length-1; Y = 0 (256) ->
        adc zx_dst                  ;   $FF+1 = +256, carry into the high byte
        sta zx_dst
        bcs zx_ch                   ; page step (rare), out of line
zx_c0   ldy #0
        asl zx_bitr
        bcc zx_lit
; new offset: Elias(MSB) LSB Elias(length-1); MSB 256 (X wraps to 0) ends the stream
zx_new  inx
        jsr zx_elias
        beq zx_end
        dex                         ; offset-1 = (MSB-1)*128 + LSB>>1
        txa
        lsr @
        sta zx_off+1
        lda (zx_src),y
        inc zx_src
        beq zx_rf1                  ; refill keeps C = bit 0 of MSB-1 for the ror
zx_s1   ror @
        sta zx_off
        ldx #1                      ; LSB bit 0 (now in C) = first length bit
        jsr zx_elias1
        inx
        bcc zx_copy                 ; C=0 from zx_elias: always

; interlaced Elias gamma: X = 1 in, value (8-bit, 256 -> 0) out, C = 0, Z by value
zx_eget asl zx_bitr                 ; data bit
        rol @
        tax
zx_elias
        asl zx_bitr                 ; control bit: 1 = more, 0 = end
        bne zx_elias1
        lda (zx_src),y              ; reservoir empty (sentinel out, C=1): next byte
        inc zx_src
        beq zx_rf2
zx_s2   rol @                       ; C=1 in -> the new sentinel
        sta zx_bitr
zx_elias1
        txa
        bcs zx_eget
zx_end  rts

; the 1-in-256 cases, out of the loops (each reached by a not-taken beq/bcs above)
zx_lh   inc zx_dst+1
        jmp zx_l0
; literals across the end of PKBUF (or 256 of them): a byte at a time, X = length
zx_lsl  lda (zx_src),y
        inc zx_src
        bne zx_s0
        jsr pk_refill
zx_s0   sta (zx_dst),y
        inc zx_dst
        bne zx_d0
        inc zx_dst+1
zx_d0   dex
        bne zx_lsl
        beq zx_l1                   ; Z=1 (the bne fell through); Y = 0 all along
zx_ch   inc zx_dst+1
        jmp zx_c0
zx_rf1  jsr pk_refill
        jmp zx_s1
zx_rf2  jsr pk_refill
        jmp zx_s2

        ert *>$A000                 ; fmul tables start at $A000
        ert <PKBUF<>0               ; pk_fill's bmi and the zx_src wrap need a page
