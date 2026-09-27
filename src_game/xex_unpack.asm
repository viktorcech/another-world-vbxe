;=============================================================================
; xex_unpack.asm  -  stage 2 of a packed XEX on the disk (the intro AND the game).
;
;   tools/pack_xex.py turns a plain xex (awintro.xex, awgame.xex) into: this code as
;   an ordinary XEX segment + its INI, then a record stream the boot loader never
;   parses:
;       0, lo, hi, <ZX02 stream>   data segment -> unpacked to its own address
;       1, lo, hi                  INIT: jsr lo/hi (MEMAC-B bank stubs, disable_basic)
;       2, lo, hi                  RUN : jmp lo/hi (the end)
;   in the plain xex's segment order, so every write and every INIT happens exactly
;   as before. Bytes come from the boot loader's own sector buffer and read_sec
;   (its "LOADING... nn%" counter keeps running); the decoder is the one in
;   src_game/game_unpack.asm with this byte source.
;   Lives in $0480-$05FF: pack_xex.py refuses a xex with a segment there, and it
;   is dead once the RUN record jumps -- the program reuses the page after that.
;   BOOT_* come from the assembled boot loader (pack_xex.py -d:, out/boot.lab).
;=============================================================================

        opt h+
        org $0480

zx_off  = $E2                       ; (2) match offset - 1   (the boot loader has $E0/$E1)
zx_dst  = $E4                       ; (2) output ptr
zx_bitr = $E6                       ; bit reservoir, sentinel-terminated
zx_ptr  = $E7                       ; (2) match source ptr

;-----------------------------------------------------------------------------
; iu_rec : one record per pass; the RUN record leaves through its jmp.
;-----------------------------------------------------------------------------
iu_rec  jsr zx_get                  ; tag -> X (zx_get keeps X)
        tax
        jsr zx_get
        sta zx_dst
        sta iu_jmp+1
        jsr zx_get
        sta zx_dst+1
        sta iu_jmp+2
        txa
        beq iu_data
        lsr @                       ; 1 = INIT (C=1), 2 = RUN (C=0)
        bcc iu_jmp
        jsr iu_jmp
        jmp iu_rec
iu_data jsr zx_dec
        jmp iu_rec
iu_jmp  jmp $FFFF                   ; SMC: INIT / RUN target

;-----------------------------------------------------------------------------
; zx_get : next stream byte in A. Keeps X and C (live in the decoder), returns
;   Y = 0. BOOT_BUFPOS is 0..128; bit 7 = sector used up.
;-----------------------------------------------------------------------------
zx_get  ldy BOOT_BUFPOS
        bmi zx_rd                   ; 1 in 128, out of line
zx_g1   lda BOOT_SECBUF,y
        iny
        sty BOOT_BUFPOS
        ldy #0
        rts
zx_rd   php
        stx zx_xs
        jsr BOOT_READSEC            ; next sector, cur_sec++, percent, BUFPOS = 0
        ldx zx_xs
        plp
        ldy #0
        beq zx_g1                   ; always
zx_xs   dta 0

;-----------------------------------------------------------------------------
; zx_dec : decode one ZX02 stream to zx_dst (DMSC's zx02-optim, MIT).
;-----------------------------------------------------------------------------
zx_dec  ldy #0
        sty zx_off                  ; initial offset 1 (zx02's default -o 1)
        sty zx_off+1
        lda #$80
        sta zx_bitr
; literal run: Elias(length) byte[1] .. byte[length]
zx_lit  ldx #1
        jsr zx_elias
zx_cp0  jsr zx_get
        sta (zx_dst),y
        inc zx_dst
        beq zx_dh0                  ; next output page (1 in 256), out of line
zx_d0   dex
        bne zx_cp0
        asl zx_bitr
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
        jsr zx_get                  ; keeps C = bit 0 of MSB-1 for the ror
        ror @
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
        jsr zx_get                  ; reservoir empty (sentinel out, C=1 kept)
        rol @                       ; C=1 in -> the new sentinel
        sta zx_bitr
zx_elias1
        txa
        bcs zx_eget
zx_end  rts

; the 1-in-256 cases, out of the loops
zx_dh0  inc zx_dst+1
        jmp zx_d0
zx_ch   inc zx_dst+1
        jmp zx_c0

        ert *>$0600                 ; $0600 = the intro's bank stubs (INIT records)

        ini iu_rec
