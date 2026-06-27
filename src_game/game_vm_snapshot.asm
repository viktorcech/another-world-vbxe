;=============================================================================
; game_vm_snapshot.asm  --  freeze & thaw a whole scene, so 'C' can RESUME it.
;
;   Pressing 'C' jumps to the access-code / password screen (part 16008); pressing
;   ESC there should drop you back EXACTLY where you were, not restart the scene.
;   To make that work we take a full snapshot before leaving and put it back on
;   return:
;     snapshot_state  saves the live VM state -- all 64 thread PCs, the pause /
;                     request arrays, the page + palette globals, and the 256
;                     variables -- into the SNAP buffer in spare RAM.
;     pages_xfer      copies the four screen pages into VRAM holes part 16008 never
;                     touches (and copies them back, the reverse direction, on ESC).
;     restore_state   reloads all of the above + re-applies the saved palette and
;                     re-shows the page, so the scene continues mid-action.
;
;   Part of the game_vm split.
;=============================================================================

;=============================================================================
; snapshot_state / restore_state : save & restore the whole VM scene state (64-thread
; PCs/pause/requests + page/palette/hold globals + the 256 vars) to SNAP, so pressing
; C -> access-code -> ESC RESUMES the scene instead of restarting it. The VRAM resource
; banks are restreamed by load_part; the framebuffer PAGES + applied palette go through
; pages_xfer / vm_lastpal (see below) -- 16008 draws over every page and runs scripts
; that clobber the vars, so both must come back on the ESC return.
;=============================================================================
.proc snapshot_state
        ldx #0
?l      lda tpc_lo,x
        sta SNAP+0,x
        lda tpc_hi,x
        sta SNAP+64,x
        lda tpause,x
        sta SNAP+128,x
        lda treq_lo,x
        sta SNAP+192,x
        lda treq_hi,x
        sta SNAP+256,x
        inx
        cpx #64
        bne ?l
        lda vm_cur1
        sta SNAP_G+0
        lda vm_cur2
        sta SNAP_G+1
        lda vm_cur3
        sta SNAP_G+2
        lda vm_pend
        sta SNAP_G+3
        lda vm_hold
        sta SNAP_G+4
        lda vm_lastpal
        sta SNAP_G+5
        ldx #0                      ; the 256 vars too (16008's scripts clobber them)
?v      lda var_lo,x
        sta SNAP_V,x
        lda var_hi,x
        sta SNAP_V+256,x
        inx
        bne ?v
        rts
.endp

.proc restore_state
        ldx #0
?l      lda SNAP+0,x
        sta tpc_lo,x
        lda SNAP+64,x
        sta tpc_hi,x
        lda SNAP+128,x
        sta tpause,x
        lda SNAP+192,x
        sta treq_lo,x
        lda SNAP+256,x
        sta treq_hi,x
        inx
        cpx #64
        bne ?l
        lda SNAP_G+0
        sta vm_cur1
        lda SNAP_G+1
        sta vm_cur2
        lda SNAP_G+2
        sta vm_cur3
        lda SNAP_G+3
        sta vm_pend
        lda SNAP_G+4
        sta vm_hold
        lda vm_cur1                 ; draw page = restored cur1 ; resync cbase for fill_span
        sta cur_draw
        jsr set_cbase_cur
        lda #1                      ; the snapshot may hold pending treq/tpreq ->
        sta req_any                 ;   force the next apply scan
        ldx #0                      ; bring the var file back (hero x/y/state/...)
?v      lda SNAP_V,x
        sta var_lo,x
        lda SNAP_V+256,x
        sta var_hi,x
        inx
        bne ?v
        lda SNAP_G+5                ; re-apply the scene's palette: 16008 loaded its own
        sta vm_lastpal              ;   (green) one, and a resumed scene may not set a
        cmp #$FF                    ;   palette again for a long time
        beq ?npal                   ; (load_part already restreamed this part's pal_data)
        jsr set_palette
?npal   lda vm_cur2                 ; re-show the restored display page now (don't wait
        jsr show_page               ;   for the scene's next blit op)
        rts
.endp

;=============================================================================
; pages_xfer : copy all 4 LR pages between VRAM and the PSAV0-3 snapshot slots.
;   vm_s1 = 0 : pages -> slots (entering 16008 by 'C')
;   vm_s1 = 1 : slots -> pages (ESC return; MUST run BEFORE load_part, which
;               restreams poly/code/v2/sfx over the slots)
;   Same proven blit geometry as copy_page (WIDTH-1=159, HEIGHT-1=199, STEPY=160),
;   just with 24-bit src/dst bases; STEPYs are forced to the LR stride because the
;   restore runs while the SR (16008) mode is still active. Pages are only ever
;   saved from LR parts ('C' is ignored on parts 0/8), so 32000 B/page is right.
;=============================================================================
.proc pages_xfer
        ldx #0
?l      jsr blit_idle               ; the BCB must be idle before editing
        lda #0
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
        jsr blit_idle               ; the last copy must land before SIO / display use
        lda #$FF                    ; the span BCB mode fields were clobbered
        sta last_scol
        rts
.endp
psv_mid dta >[PSAV0&$FFFF], >[PSAV1&$FFFF], >[PSAV2&$FFFF], >[PSAV3&$FFFF]
psv_hi  dta [PSAV0>>16], [PSAV1>>16], [PSAV2>>16], [PSAV3>>16]

