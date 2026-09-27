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
.if 1
        ldx #63                     ; count down: the 64 entries are independent
.else
        ldx #0
.endif
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
.if 1
        dex
        bpl ?l
.else
        inx
        cpx #64
        bne ?l
.endif
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

.if 1
        nocross restore_state, restore_state.rs_l, restore_state.rs_le   ; the 64-pass loop edge
.endif
.proc restore_state
.if 1
        ldx #63                     ; count down: the 64 entries are independent
.else
        ldx #0
.endif
rs_l
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
.if 1
        dex
        bpl ?l
rs_le
.else
        inx
        cpx #64
        bne ?l
.endif
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
        lda #64                     ; the restore wrote tpc[] directly (bypassing the
        sta vm_maxt                 ;   apply scan), so the run-loop watermark must
                                    ;   cover ALL threads again (over-scan is safe)
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
        jmp show_page               ;   for the scene's next blit op)
        ;rts
.endp

; pages_xfer + psv_mid/psv_hi moved to src_game/game_vm_xfer.asm (COLD -> out
; of the $2000 hot chain, into the $AA00 gap after the cellcache hot block).

