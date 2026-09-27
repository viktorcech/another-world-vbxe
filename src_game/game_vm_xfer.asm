;=============================================================================
; pages_xfer : copy all 4 LR pages between VRAM and the PSAV0-3 snapshot slots.
;   vm_s1 = 0 : pages -> slots (entering 16008 by 'C')
;   vm_s1 = 1 : slots -> pages (ESC return; MUST run BEFORE load_part, which
;               restreams poly/code/v2/sfx over the slots)
;   Same proven blit geometry as copy_page (WIDTH-1=159, HEIGHT-1=199, STEPY=160),
;   just with 24-bit src/dst bases; STEPYs are forced to the LR stride because the
;   restore runs while the SR (16008) mode is still active. Pages are only ever
;   saved from LR parts ('C' is ignored on parts 0/8), so 32000 B/page is right.
;
; COLD (runs only on the 'C' code screen / ESC return), so it lives OUT of the
; $2000 hot chain: icl'd right after game_cellcache.asm, it assembles at the
; cellcache hot block's end inside the guarded $AA00-$AFFF gap (jsr-only entry,
; no fall-through -- 6502-cycles-layout: a rare block out of line always pays).
;=============================================================================
.proc pages_xfer
.if 1
        ; vbxe-blitter skill: the blitter reads a BCB only at START (fire_fill waits
        ; before it), so the fields every copy shares are written ONCE, and the
        ; direction is picked once -- each copy writes only its two addresses.
        lda #0
        sta BCB+BCB_SRC_ADDR
        sta BCB+BCB_DST_ADDR
        sta BCB+BCB_XOR
        lda #<SCRW                  ; LR page geometry regardless of the current
        sta BCB+BCB_SRC_STEPY       ;   render mode (SR is active during the restore)
        sta BCB+BCB_DST_STEPY
        lda #>SCRW
        sta BCB+BCB_SRC_STEPY+1
        sta BCB+BCB_DST_STEPY+1
        lda #1
        sta BCB+BCB_SRC_STEPX
        lda #<(SCRW-1)
        sta BCB+BCB_WIDTH
        lda #>(SCRW-1)
        sta BCB+BCB_WIDTH+1
        lda #SCRH-1
        sta BCB+BCB_HEIGHT
        lda #$FF
        sta BCB+BCB_AND
        lda #BLT_COPY
        sta BCB+BCB_CTRL
        ldx #3                      ; count down: the 4 pages are independent
        lda vm_s1
        bne ?rl
?sl     lda #0                      ; SAVE : src = page X ($00:pg:00:00)
        sta BCB+BCB_SRC_ADDR+1
        stx BCB+BCB_SRC_ADDR+2
        lda psv_mid,x               ;        dst = slot X
        sta BCB+BCB_DST_ADDR+1
        lda psv_hi,x
        sta BCB+BCB_DST_ADDR+2
        jsr fire_fill
        dex
        bpl ?sl
        bmi ?xd
?rl     lda psv_mid,x               ; RESTORE : src = slot X
        sta BCB+BCB_SRC_ADDR+1
        lda psv_hi,x
        sta BCB+BCB_SRC_ADDR+2
        lda #0                      ;           dst = page X
        sta BCB+BCB_DST_ADDR+1
        stx BCB+BCB_DST_ADDR+2
        jsr fire_fill
        dex
        bpl ?rl
?xd
.else
        ldx #0
?l      lda #0                      ; (fire_fill waits before its START)
        sta BCB+BCB_SRC_ADDR
        sta BCB+BCB_DST_ADDR
        lda vm_s1
        bne ?rest
        lda #0                      ; SAVE : src = page X ($00:pg:00:00)
        sta BCB+BCB_SRC_ADDR+1
        stx BCB+BCB_SRC_ADDR+2
        lda psv_mid,x               ;        dst = slot X
        sta BCB+BCB_DST_ADDR+1
        lda psv_hi,x
        sta BCB+BCB_DST_ADDR+2
        jmp ?go
?rest   lda psv_mid,x               ; RESTORE : src = slot X
        sta BCB+BCB_SRC_ADDR+1
        lda psv_hi,x
        sta BCB+BCB_SRC_ADDR+2
        lda #0                      ;           dst = page X
        sta BCB+BCB_DST_ADDR+1
        stx BCB+BCB_DST_ADDR+2
?go     lda #<SCRW                  ; LR page geometry regardless of the current
        sta BCB+BCB_SRC_STEPY       ;   render mode (SR is active during the restore)
        sta BCB+BCB_DST_STEPY
        lda #>SCRW
        sta BCB+BCB_SRC_STEPY+1
        sta BCB+BCB_DST_STEPY+1
        lda #1
        sta BCB+BCB_SRC_STEPX
        lda #<(SCRW-1)
        sta BCB+BCB_WIDTH
        lda #>(SCRW-1)
        sta BCB+BCB_WIDTH+1
        lda #SCRH-1
        sta BCB+BCB_HEIGHT
        lda #$FF
        sta BCB+BCB_AND
        lda #0
        sta BCB+BCB_XOR
        lda #BLT_COPY
        sta BCB+BCB_CTRL
        jsr fire_fill
        inx
        cpx #4
        bne ?l
.endif
        jsr blit_idle               ; the last copy must land before SIO / display use
        lda #$FF                    ; the span BCB mode fields were clobbered
        sta last_scol
        sta bcb_pg                  ; (the span BCB page / height shadow too)
        rts
.endp
psv_mid dta >[PSAV0&$FFFF], >[PSAV1&$FFFF], >[PSAV2&$FFFF], >[PSAV3&$FFFF]
psv_hi  dta [PSAV0>>16], [PSAV1>>16], [PSAV2>>16], [PSAV3>>16]
        ert *>$AFFF                 ; must stay inside the cellcache gap ($AA00-$AFFF)
