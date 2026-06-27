;=============================================================================
; game_data.asm  -  data for the GAME build (separate from the intro).
;
;   Phase 2: resources are NO LONGER streamed into VRAM at build time -- the
;   runtime loader (game_diskio.asm) reads each part from out/game.atr on demand.
;   This file now only provides the RAM-resident generic tables and the palette
;   buffer the disk loader fills per part.
;
;   Build data:  python tools/make_game_atr.py   (out/game.atr + game_atr.inc)
;   recip / row tables live inside src/aw_vbxe.asm (self-contained).
;=============================================================================
        org $9000
pal_data
        ins 'out/water_pal.bin'             ; default palette buffer; overwritten per
                                            ; part by load_part (32 pals x 16 x RGB)

;-----------------------------------------------------------------------------
; fmulu square tables (page-aligned, generic; same as the intro).
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
; poly_fetch MEMAC-B bank/window LUTs (page-aligned, generic; same as the intro).
;-----------------------------------------------------------------------------
        org $A800
poly_bank_lut
        ins 'out/polylut.bin', 0, 256
        org $A900
poly_win_lut
        ins 'out/polylut.bin', 256, 256

        run game_start
