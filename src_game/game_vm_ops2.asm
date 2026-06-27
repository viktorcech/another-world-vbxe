;=============================================================================
; game_vm_ops2.asm  --  opcode handlers 0x0B-0x10 : the "draw to screen" group.
;
;   Everything about the framebuffer pages and what you actually see: queue a
;   palette change for the next flip (setpal), choose which page draws go to
;   (selpage), clear a page to a colour (fillpage), copy one page onto another
;   (copypage -- plus copy_page_vs, the variant that honours the vertical scroll the
;   jail / elevator scenes use), and updatedisplay, which shows a page, applies the
;   deferred palette and PACES the frame so the game runs at AW's intended speed.
;   resettask pauses or removes a whole range of threads at once.
;
;   Part of the game_vm split.
;=============================================================================

op_setpal                            ; 0x0B : nextpal = w() >> 8  (deferred)
        m_vm_w
        lda vm_s2                   ; high byte = palette index
        sta vm_pend
        jmp vm_fetch

op_resettask                         ; 0x0C : reset/pause a thread range
        mfetch
        sta vm_s1                   ; first
        mfetch
        sta vm_s2                   ; last
        mfetch
        sta vm_op                   ; typ
        lda vm_s2
        cmp vm_s1
        bcc ?rtdone                 ; last < first -> nothing
        lda #1
        sta req_any                 ; requests are pending -> next apply scan runs
        ldx vm_s1
?rtloop lda vm_op
        cmp #2
        bne ?pause
        lda #$FE                    ; typ 2 : remove (treq = $FFFE)
        sta treq_lo,x
        lda #$FF
        sta treq_hi,x
        jmp ?rtnext
?pause  lda vm_op                   ; else : tpause_req = typ
        sta tpreq,x
?rtnext cpx vm_s2
        beq ?rtdone
        inx
        jmp ?rtloop
?rtdone jmp vm_fetch

op_selpage                           ; 0x0D : cur1 = page(b()) ; draw there
        mfetch
        jsr vm_page
        sta vm_cur1
        sta cur_draw
        jsr set_cbase_cur
        jmp vm_fetch

op_fillpage                          ; 0x0E : clear page(b()) to colour b()
        mfetch
        jsr vm_page
        pha
        mfetch
        tax                         ; X = colour
        pla                         ; A = physical page
        jsr clear_page
        jmp vm_fetch

op_copypage                          ; 0x0F : copy src(i) -> dst(j)
        mfetch
        sta vm_s1                   ; i
        mfetch
        jsr vm_page                 ; j -> physical
        sta cp_dst
        lda vm_s1
        cmp #$FE
        bcs ?srcFE                  ; i >= 0xFE -> page(i), plain copy
        and #$80
        bne ?src80                  ; 0x80 <= i < 0xFE -> page(i & 3), SCROLLED copy
        lda vm_s1                   ; i < 0x80 -> page(i), plain copy
        jsr vm_page
        sta cp_src
        jsr copy_page
        jmp vm_fetch
?src80  lda vm_s1                   ; scrolled copy of page(i & 3) by VAR_SCROLL_Y
        and #3
        jsr vm_page
        sta cp_src
        jsr copy_page_vs
        jmp vm_fetch
?srcFE  lda vm_s1
        jsr vm_page
        sta cp_src
        jsr copy_page
        jmp vm_fetch

;-----------------------------------------------------------------------------
; copy_page_vs : copypage that HONOURS VAR_SCROLL_Y (var 0xF9). GAME-only -- the
; shared copy_page (src/aw_vbxe.asm, intro-frozen) ignores the scroll. The elevator
; shaft (jail) tiles its rocky wall by repeated scrolled copies (copypage <0x80+page>
; while the script bumps VAR_SCROLL_Y); without the scroll every tile landed on top,
; so the floor + lower walls were dropped. Mirrors game_sim GameVM.op_copypage /
; another.js copy_page. LR only (gameplay scroll scenes are LR; SR/16008 never
; scrolls). cp_src -> cp_dst. Row N byte offset = N*SCRW = row_lo/row_hi[N] + ROWBIAS.
;-----------------------------------------------------------------------------
copy_page_vs
.ifdef HIRES_CAP
        lda hires
        bne ?plain                  ; SR mode never scrolls -> plain full copy
.endif
        ldx #$F9                    ; VAR_SCROLL_Y
        lda var_hi,x
        beq ?down                   ; hi == 0 -> positive scroll, var_lo = magnitude
        cmp #$FF
        bne ?plain                  ; |scroll| >= 256 rows -> off-screen, plain copy
        lda var_lo,x                ; hi == FF -> negative scroll (content moves up)
        beq ?plain                  ; -256 -> off-screen
        eor #$FF
        clc
        adc #1                      ; mag = -scroll (1..255)
        cmp #SCRH
        bcs ?plain                  ; >= 200 rows visible-none -> plain
        sta cp_vs
        lda #0                      ; dir = up : offset the SRC address
        sta cp_vd
        jmp ?blit
?plain  jmp copy_page               ; (local trampoline: copy_page may be > 127B away)
?down   lda var_lo,x
        beq ?plain                  ; 0 -> plain copy
        cmp #SCRH
        bcs ?plain                  ; >= 200 -> off-screen
        sta cp_vs
        lda #1                      ; dir = down : offset the DST address
        sta cp_vd
?blit   jsr blit_idle
        lda #0                      ; both addresses = (0,0,page) to start
        sta BCB+BCB_SRC_ADDR
        sta BCB+BCB_SRC_ADDR+1
        sta BCB+BCB_DST_ADDR
        sta BCB+BCB_DST_ADDR+1
        lda cp_src
        sta BCB+BCB_SRC_ADDR+2
        lda cp_dst
        sta BCB+BCB_DST_ADDR+2
        ldx cp_vs                   ; offset (lo,hi) = mag*SCRW = row_lut[mag] + ROWBIAS
        lda row_lo,x                ; (<ROWBIAS = 0, so the low byte is unchanged)
        ldy row_hi,x
        pha                         ; save offset lo
        tya
        clc
        adc #>ROWBIAS               ; + high byte of ROWBIAS ($40 for LR)
        tay                         ; Y = offset hi
        pla                         ; A = offset lo
        ldx cp_vd
        beq ?usrc                   ; dir 0 = up -> offset SRC; else down -> offset DST
        sta BCB+BCB_DST_ADDR
        sty BCB+BCB_DST_ADDR+1
        jmp ?geom
?usrc   sta BCB+BCB_SRC_ADDR
        sty BCB+BCB_SRC_ADDR+1
?geom   lda #<SCRW                  ; LR stride for both src & dst
        sta BCB+BCB_SRC_STEPY
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
        sec                         ; HEIGHT = (SCRH-1) - mag  (blit copies HEIGHT+1 rows)
        lda #SCRH-1
        sbc cp_vs
        sta BCB+BCB_HEIGHT
        lda #$FF
        sta BCB+BCB_AND
        lda #0
        sta BCB+BCB_XOR
        lda #BLT_COPY
        sta BCB+BCB_CTRL
        lda #$FF                    ; copy clobbered the span BCB mode fields
        sta last_scol
        jsr fire_fill
        rts

op_updatedisplay                     ; 0x10 : show page, apply deferred palette, hold
        mfetch
        cmp #$FE
        beq ?nopg
        cmp #$FF
        bne ?setc2
        lda vm_cur2                 ; 0xFF : swap cur2,cur3
        ldx vm_cur3
        sta vm_cur3
        stx vm_cur2
        jmp ?nopg
?setc2  jsr vm_page
        sta vm_cur2
?nopg   jsr blit_idle               ; finish the last poly span
        ; --- pace: spin until the frame is DUE. This ABSORBS the render time that
        ;     already elapsed, so the frame-to-frame interval is exactly VAR_PAUSE_
        ;     SLICES vblanks (not render+hold). RTCLOK3 ticks once per vblank (VBI);
        ;     we exit right at a tick, so the page flip below is tear-free. (Old code
        ;     waited hold vblanks AFTER the render -> ran ~30-48% too slow on Rapidus.)
?wd     lda pace_due
        sec
        sbc RTCLOK3                 ; due - now (signed 8-bit)
        beq ?due                    ; exactly due (at a vblank tick) -> show tear-free
        bpl ?wd                     ; due still ahead -> spin (NMI advances RTCLOK3)
        jsr wait_vblank             ; OVERRAN (slow CPU): late -> sync to vblank so the
                                    ;   page flip is still tear-free (else stock 6502 tears)
?due    lda vm_pend                 ; apply any deferred palette (at the vblank tick)
        bmi ?nopal
        sta vm_lastpal              ; remember the APPLIED palette (ESC-resume re-applies)
        jsr set_palette
        lda #$FF
        sta vm_pend
?nopal  lda vm_cur2
        jsr show_page
        ldx #$FF                    ; hold = var[0xFF] (>=1) vblanks per frame
        lda var_lo,x
        bne ?h1
        lda #1
?h1     sta vm_hold
        ; --- NTSC speed compensation. AW data is timed for 50 Hz (PAL); an NTSC vblank
        ;     is 60 Hz, so holding N vblanks runs 60/50 = 1.2x too FAST. Add hold/5 extra
        ;     vblanks (-> hold*1.2); pace_frac (fifths) carries the remainder so the
        ;     long-run average is exact. is_pal != 0 = PAL (unchanged); == 0 = NTSC.
        lda is_pal
        bne ?paced
        lda pace_frac
        clc
        adc vm_hold                 ; frac += base hold (accumulate fifths of a vblank)
?f5     cmp #5
        bcc ?fdone                  ; < 5 -> keep as remainder
        sbc #5                      ; carry set by cmp>=5 -> exact -5
        inc vm_hold                 ; one extra vblank this frame
        jmp ?f5
?fdone  sta pace_frac
?paced  lda vm_hold
        clc                         ; pace_due += hold (NTSC-adjusted; drift-free cadence)
        adc pace_due
        sta pace_due
        sec
        sbc RTCLOK3                 ; if we've fallen behind (render overran), resync
        bpl ?cont
        lda RTCLOK3                 ; deadline = now + hold
        clc
        adc vm_hold
        sta pace_due
?cont   jmp vm_fetch

