;=============================================================================
; Big data : above the MEMAC-A window (RAM at $9000+, never inside a window)
;=============================================================================
        org $9000
pal_data
        ins 'out/intro_pal.bin'             ; 32 palettes x 16 x RGB (7-bit)

;-----------------------------------------------------------------------------
; fmulu square tables (8x8->16 multiply, src/aw_polygon.asm).  4 x 512 B = 2 KB.
;   Each MUST be page-aligned: the factor 'a' is the self-modified low byte of
;   the table base, so the base low byte must be $00.  a+b reaches 510 -> 512 B.
;-----------------------------------------------------------------------------
        org $A000
fmul_sq1l
        ins 'out/fmul.bin', 0, 512
        org $A200
fmul_sq1h
        ins 'out/fmul.bin', 512, 512
        org $A400
fmul_sq2l
        ins 'out/fmul.bin', 1024, 512
        org $A600
fmul_sq2h
        ins 'out/fmul.bin', 1536, 512

;-----------------------------------------------------------------------------
; set_poly_ptr MEMAC-B bank/window LUTs (indexed by dr_off hi) : replace the
; 6x lsr + add/or arithmetic on a stream re-sync.  Page-aligned (256-entry, x=0..255 -> no
; page-cross penalty).  Values are bit-identical to the calc (tools/gen_polylut.py).
;-----------------------------------------------------------------------------
        org $A800
poly_bank_lut
        ins 'out/polylut.bin', 0, 256
        org $A900
poly_win_lut
        ins 'out/polylut.bin', 256, 256

;-----------------------------------------------------------------------------
; Text : AW 8x8 font + the intro string table (op_drawtext / playlist 0x07).
;   ~1.8 KB ; placed above the RAMB work area ($9C00..) and below ROM ($C000).
;-----------------------------------------------------------------------------
        org $B000
        icl 'src/aw_text_data.inc'          ; aw_font, aw_id_lo/hi, aw_str_lo/hi, aw_strbytes

;=============================================================================
; Stream the poly data into VRAM at load time, via MEMAC-B.
;   Each chunk: a stub sets the MEMAC-B bank (run by the loader through INI),
;   then 16K of poly bytes are loaded into the $4000 window -> that VRAM bank.
;   $050000 = bank $14, so chunk K -> bank $14+K.
;=============================================================================
        org $0600
?sb14   lda #$80+POLY_BANK0+0
        sta VBXE_MEMAC_B
        rts
        ini ?sb14
        org DATAW
        ins 'out/intro_poly.bin', $0000, $4000

        org $0600
?sb15   lda #$80+POLY_BANK0+1
        sta VBXE_MEMAC_B
        rts
        ini ?sb15
        org DATAW
        ins 'out/intro_poly.bin', $4000, $4000

        org $0600
?sb16   lda #$80+POLY_BANK0+2
        sta VBXE_MEMAC_B
        rts
        ini ?sb16
        org DATAW
        ins 'out/intro_poly.bin', $8000, $4000

        org $0600
?sb17   lda #$80+POLY_BANK0+3
        sta VBXE_MEMAC_B
        rts
        ini ?sb17
        org DATAW
        ins 'out/intro_poly.bin', $C000, 16078     ; 65230 - 49152

;=============================================================================
; Stream the full playlist into VRAM ($060000 = bank $18), same INI pattern.
;   intro_playlist.bin = 104053 bytes -> 7 banks ($18..$1E).
;=============================================================================
        org $0600
?sp18   lda #$80+PLAY_BANK0+0
        sta VBXE_MEMAC_B
        rts
        ini ?sp18
        org DATAW
        ins 'out/intro_playlist.bin', $00000, $4000

        org $0600
?sp19   lda #$80+PLAY_BANK0+1
        sta VBXE_MEMAC_B
        rts
        ini ?sp19
        org DATAW
        ins 'out/intro_playlist.bin', $04000, $4000

        org $0600
?sp1A   lda #$80+PLAY_BANK0+2
        sta VBXE_MEMAC_B
        rts
        ini ?sp1A
        org DATAW
        ins 'out/intro_playlist.bin', $08000, $4000

        org $0600
?sp1B   lda #$80+PLAY_BANK0+3
        sta VBXE_MEMAC_B
        rts
        ini ?sp1B
        org DATAW
        ins 'out/intro_playlist.bin', $0C000, $4000

        org $0600
?sp1C   lda #$80+PLAY_BANK0+4
        sta VBXE_MEMAC_B
        rts
        ini ?sp1C
        org DATAW
        ins 'out/intro_playlist.bin', $10000, $4000

        org $0600
?sp1D   lda #$80+PLAY_BANK0+5
        sta VBXE_MEMAC_B
        rts
        ini ?sp1D
        org DATAW
        ins 'out/intro_playlist.bin', $14000, $4000

        org $0600
?sp1E   lda #$80+PLAY_BANK0+6
        sta VBXE_MEMAC_B
        rts
        ini ?sp1E
        org DATAW
        ins 'out/intro_playlist.bin', $18000         ; remaining bytes to EOF (auto-size)

;=============================================================================
; Stream the SFX sample blob into VRAM ($038000 = bank $0E, the free space
; between page 3 and the control bank). intro_sfx.bin = 20635 B -> 2 banks
; ($0E,$0F). Read by the IRQ in src/aw_sound.asm via the MEMAC-B window.
; (If the blob ever exceeds 32 KB, add a bank $10 chunk -- but that hits the
;  control bank, so trim/repoint instead. gen_intro_sfx.py guards this.)
;=============================================================================
        org $0600
?ss0E   lda #$80+$0E
        sta VBXE_MEMAC_B
        rts
        ini ?ss0E
        org DATAW
        ins 'out/intro_sfx.bin', $0000, $4000

        org $0600
?ss0F   lda #$80+$0F
        sta VBXE_MEMAC_B
        rts
        ini ?ss0F
        org DATAW
        ins 'out/intro_sfx.bin', $4000               ; remaining bytes to EOF

        run start
