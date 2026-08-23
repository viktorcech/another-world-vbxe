;=============================================================================
; aw_settings.asm - pre-intro sound-output menu: POKEY 4-bit, or COVOX 8-bit at
; one of the four addresses a covox is actually wired to on this machine.
;
;   Called once from `start`, after font_init and BEFORE snd_init. At that point
;   the VBXE overlay is ON, page 0 is the displayed page (cleared black), the
;   font glyph cells are in VRAM, and no IRQ is hooked yet -- which is exactly
;   why the menu has to run here: snd_go_covox patches the VIMIRQ immediates
;   INSIDE snd_init, so the mode has to be known before that runs.
;
;   Draws to page 0 and blocks on the console keys:
;     SELECT = next option   OPTION = play a test sound   START = begin
;   The selection is shown by COLOUR (selected = yellow idx2, other = white
;   idx1) -- no moving cursor: BSTENCIL skips src==0, so re-drawing the same
;   option line in a new colour overwrites exactly the glyph pixels (the shape
;   is identical), cleanly recolouring it. On START it calls snd_set_mode, which
;   is what snd_init (this xex) and the GAME xex both read.
;
;   WHY A MENU AND NOT A PROBE. A covox is a write-only latch. At $D280 it can
;   still be found -- a PokeyMAX hands back what was written, and a card that
;   swallows the write stops POKEY from seeing it, which is observable (see
;   snd_detect). At $D500/$D600/$D700 there is NOTHING to observe: no read-back,
;   and nothing else answers there either, so no swallowed write to notice. No
;   program can autodetect those, only be told. And they are all in use:
;     $D280  PokeyMAX (and Altirra's Covox device at base $D280) -- PROBED, so
;            this option is preselected on a machine that answers there
;     $D500  p-covox jumper 1 -- also cartridge bank switching (SpartaDOS X,
;            MaxFlash, SIDE, The!Cart): a write there can bank the cart out
;     $D600  p-covox jumper 2 -- also VBXE ($D600-$D65F), and this engine only
;            runs with VBXE at $D600 (detect_vbxe), so a card that decodes the
;            whole page latches every display register write. Only the
;            $D600-$D63F variant is usable here.
;     $D700  p-covox jumper 3 -- nothing else in this machine answers there, so
;            it is the sane choice for a PBI-area card
;   So: probe $D280 (that one is free), preselect the answer, and let the user
;   pick anything -- with a TEST SOUND, which is the only honest "detection" for
;   the other three: press OPTION, hear it or don't. It plays a real intro SFX
;   (snd_preview), not a beep: a beep would prove that a wire is connected, a
;   sample proves the whole path -- the VRAM walk, the curve, the mix centre.
;
;   Reuses the text engine (draw_glyph / line_clip / the glyph BCB) from
;   aw_text.asm and the page/palette helpers from aw_vbxe.asm. Runs entirely
;   before the first 0x07 op, so it is free to use the op_drawtext scratch
;   (txt_ptr / txt_x / txt_y / txt_col / t_cx / t_ch / t_vis / t_gh). Each
;   routine is a .proc so its `?` temp labels stay isolated (mads scopes them
;   per-proc; non-proc `?` labels share one global area and would clash with
;   op_drawtext).
;=============================================================================
CONSOL  = $D01F                      ; bit0 START, bit1 SELECT, bit2 OPTION
                                     ;   (0 = pressed); write bit3 = speaker
OPT_X   = 5                          ; option text column (0..39, 8 px each)
OPT_Y0  = 80                         ; first option row (px), 16 px pitch
NOPT    = 5                          ; POKEY + four covox bases

.proc snd_settings
        lda #8
        sta CONSOL                   ; speaker off, console keys readable
        jsr snd_probe                ; $D280 only -- see the header
        bcc ?nopm
        lda #1                       ; a covox answered there: preselect it and
        sta set_sel                  ;   say so on its line
        sta pm_seen
?nopm   lda #0                       ; draw to page 0 (already the displayed page)
        sta cur_draw
        jsr set_cbase_cur
        ldx #0                       ; clear page 0 to colour index 0 (background)
        lda #0
        jsr clear_page
        jsr set_settings_pal         ; pal #1: idx0 bg / idx1 white / idx2 yellow
        jsr text_setup               ; glyph BCB constants (clear_page clobbered them)
        jsr draw_statics
        jsr draw_options
        lda #0
        jsr show_page
?loop   jsr read_console             ; A = 1 SELECT / 2 START / 3 OPTION
        cmp #2
        beq ?begin
        cmp #3
        beq ?test
        lda set_sel                  ; SELECT -> next option (wraps), redraw
        clc
        adc #1
        cmp #NOPT
        bcc ?st
        lda #0
?st     sta set_sel
        jsr draw_options
        jmp ?loop
?test   jsr snd_preview              ; OPTION -> a real SFX out of the chosen port
        jmp ?loop
?begin  ldx #0                       ; the menu text is ON page 0, and page 0 is
        lda #0                       ;   what the first playlist frame draws to:
        jsr clear_page               ;   black it again (clear_page idles the
                                     ;   blitter and re-arms last_scol itself)
        jsr blit_idle
        lda #<SCRW                   ; text_setup left SRC_STEPY = 4 (glyph stride);
        sta BCB+BCB_SRC_STEPY        ;   a later 2-tall copy span would read row 2
        lda #>SCRW                   ;   from src+4 instead of src+SCRW (mirror
        sta BCB+BCB_SRC_STEPY+1      ;   op_drawtext's teardown)
        lda #0
        sta cur_draw
        jsr set_cbase_cur
        lda set_sel
        jsr snd_set_mode             ; hand the answer to snd_init (and the game)
        rts
.endp

;-----------------------------------------------------------------------------
; snd_set_mode : A = the menu's answer (0 = POKEY, 1..4 = covox base index+1).
;   Stores it where snd_init reads it, and leaves a copy in the $04FE/$04FF
;   handoff cell so the GAME -- a separate xex the boot loader chain-loads over
;   this one (aw_exit.asm) -- starts in the same mode without asking again.
;-----------------------------------------------------------------------------
.proc snd_set_mode
        sta snd_mode
        sta CFG_MODE
        lda #CFG_OK
        sta CFG_MAGIC
        rts
.endp

;-----------------------------------------------------------------------------
; snd_preview : play ONE random GAME sound through the CURRENTLY SELECTED output.
;
;   This is the only honest test for the three PBI bases -- they cannot be probed
;   (see the header), so the answer is "press OPTION and listen". The sounds are
;   real game SFX, baked into this build by tools/gen_test_sfx.py because the
;   menu runs long before any game data is on the machine; they sit in the dead
;   tail of the last music VRAM bank. A beep would only prove that a wire is
;   connected -- this walks a real sample stream, through the same volume curve,
;   around the same mix centre as the IRQ does in the game. The pacing is a delay
;   loop instead of Timer 1, because at menu time the IRQ is not hooked yet and
;   snd_go_covox -- a ONE-WAY switch -- must not happen until the user has
;   actually chosen.
;
;   $D280 with the probe saying NONE is deliberately SILENT. There, base+1 is
;   AUDC1 on a stock POKEY and the linear covox curve (n*8 + 64) has volume bits
;   set in half its entries, so playing it would make POKEY noise and the user
;   would "hear a covox" that three probes just proved is not there. The other
;   three bases need no such care: nothing else in the machine answers them.
;
;   ~444 cycles per nibble = the ~3995 Hz the samples were baked at. Each sound
;   is under half a second by construction, and the window-end check is a belt-
;   and-braces stop: nothing here walks a bank list the way the IRQ does.
;-----------------------------------------------------------------------------
PRV_D   = 79                         ; delay iterations: 5*79-1 + ~50 cyc of work

.proc snd_preview
        lda #0
        sta AUDCTL
        sta AUDC1
        sta AUDC2
        sta AUDC3
        sta AUDC4
        lda #3
        sta SKCTL                    ; RANDOM below needs the polys running
        ; --- where does it go, and is it worth writing there at all? ---
        ldx set_sel
        beq ?pokey
        cpx #1
        bne ?cv                      ; $D500/$D600/$D700: nothing else answers
        lda pm_seen                  ; $D280: only when the probe found a card --
        bne ?cv                      ;   see the header
        rts                          ;   NONE: stay silent rather than answer with
                                     ;   POKEY noise the user would misread
?cv     dex                          ; 1..4 -> covox base index 0..3
        lda cv_blo,x
        sta ?o1+1
        clc
        adc #1                       ; base+0 = left, base+1 = right
        sta ?o2+1
        lda cv_bhi,x
        sta ?o1+2
        sta ?o2+2
        ldy #15                      ; the LINEAR curve at full volume, lifted by
?cl     lda voltab8+15*16,y          ;   the 64 the mix tail adds for a silent
        clc                          ;   music voice -- so the DAC swings around
        adc #64                      ;   $80 exactly as it does in the intro
        sta prv_vt,y
        dey
        bpl ?cl
        jmp ?dup
?pokey  lda #<AUDC4                  ; the SFX voice, volume-only like the player
        sta ?o1+1
        sta ?o2+1
        lda #>AUDC4
        sta ?o1+2
        sta ?o2+2
        ldy #15
?pl     lda voltab+15*16,y           ; already $10-ORed for AUDC volume-only
        sta prv_vt,y
        dey
        bpl ?pl
?dup    ; --- pick one of the baked GAME sounds (tools/gen_test_sfx.py) --------
        lda RANDOM
        and #$0F                     ; 0..15
        cmp #TST_COUNT
        bcc ?rok
        sbc #TST_COUNT               ; fold the rest down. NOT a re-roll loop: a
?rok    tax                          ;   frozen RANDOM ($FF) would spin forever
        lda #TST_BANK
        sta VBXE_MEMAC_B             ; window $4000 -> the bank they were baked in
        lda tst_winlo,x              ;   (one bank for all of them: no walk)
        sta ?rd+1
        lda tst_winhi,x
        sta ?rd+2
        sec                          ; count = -len : the loop counts UP to $0000
        lda #0
        sbc tst_lenlo,x
        sta ?nl
        lda #0
        sbc tst_lenhi,x
        sta ?nh
        sei                          ; the OS VBI would warble the sample clock
?loop   lda ?rd+2
        cmp #$80
        bcs ?end                     ; ran into the next bank: stop, no walk
?rd     lda $FFFF                    ; SMC : the sample byte -- 2 nibbles, hi first
        tax
        lsr @
        lsr @
        lsr @
        lsr @
        tay
        lda prv_vt,y
        jsr ?snd
        ldy #PRV_D
?dh     dey
        bne ?dh
        txa
        and #$0F
        tay
        lda prv_vt,y
        jsr ?snd
        inc ?rd+1
        bne ?ct
        inc ?rd+2
?ct     inc ?nl
        bne ?more
        inc ?nh
        beq ?end
?more   ldy #PRV_D
?dl     dey
        bne ?dl
        jmp ?loop
?end    lda #$80                     ; park: covox mid rail ...
        ldx set_sel
        bne ?pk
        lda #0                       ;   ... or POKEY volume off
?pk     jsr ?snd
        lda memb_cur                 ; MEMAC-B back to the engine's invariant
        sta VBXE_MEMAC_B
        cli
        rts
?snd    ; A = one sample level -> both ports of the chosen output
?o1     sta $FFFF                    ; SMC oper : AUDC4 / base+0
?o2     sta $FFFF                    ; SMC oper : AUDC4 / base+1
        rts
?nl     dta 0
?nh     dta 0
.endp

prv_vt  dta 0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0   ; the 16 levels of the chosen output

;-----------------------------------------------------------------------------
; text_setup : BCB constants for the glyph BSTENCIL blits (copied from
;   op_drawtext; the per-string colour is set in draw_str). DST/SRC step Y and
;   page are valid because clear_page left DST_STEPY = SCRW and cbase = page 0.
;-----------------------------------------------------------------------------
.proc text_setup
        jsr blit_idle
        lda cbase+2
        sta BCB+BCB_DST_ADDR+2
        lda #[FONT_V>>16]
        sta BCB+BCB_SRC_ADDR+2
        lda #4                       ; src row stride = glyph cell width (4 LR bytes)
        sta BCB+BCB_SRC_STEPY
        lda #0
        sta BCB+BCB_SRC_STEPY+1
        sta BCB+BCB_WIDTH+1
        sta BCB+BCB_XOR
        lda #1
        sta BCB+BCB_SRC_STEPX
        lda #3                       ; WIDTH-1 : 8 px = 4 LR bytes
        sta BCB+BCB_WIDTH
        lda #BLT_BSTENCIL
        sta BCB+BCB_CTRL
        rts
.endp

;-----------------------------------------------------------------------------
; draw_str : render the $00-terminated string at txt_ptr, starting at column
;   txt_x / row txt_y, colour index txt_col. Reuses draw_glyph + line_clip.
;   Clobbers X and Y (draw_glyph does) -- callers keep their index in memory.
;-----------------------------------------------------------------------------
.proc draw_str
        lda txt_col
        sta BCB+BCB_AND
        jsr line_clip                ; t_vis / t_gh from txt_y
        lda txt_x
        sta t_cx
?c      ldy #0
        lda (txt_ptr),y
        beq ?done
        inc txt_ptr
        bne ?p
        inc txt_ptr+1
?p      sta t_ch
        lda t_vis
        beq ?adv                     ; line below the screen : don't draw
        lda t_cx
        cmp #40
        bcs ?adv                     ; column >= 40 : off the right
        jsr draw_glyph
?adv    inc t_cx
        jmp ?c
?done   rts
.endp

;-----------------------------------------------------------------------------
; draw_one : X = index into the s_lo/s_hi/o_x/o_y tables, A = colour. One line.
;-----------------------------------------------------------------------------
.proc draw_one
        sta txt_col
        lda s_lo,x
        sta txt_ptr
        lda s_hi,x
        sta txt_ptr+1
        lda o_x,x
        sta txt_x
        lda o_y,x
        sta txt_y
        jmp draw_str
.endp

;-----------------------------------------------------------------------------
; draw_statics : the fixed (white) lines -- title, version, heading, the three
;   key hints -- and the probe's verdict against the $D280 line: FOUND, or NONE.
;   NONE is not decoration: it is why OPTION plays nothing on that line (see
;   snd_preview), and it is the one thing on this screen the machine knows for
;   certain.
;-----------------------------------------------------------------------------
.proc draw_statics
        ldx #NOPT                    ; entries NOPT.. are the static ones
?l      stx ?i
        lda #1
        jsr draw_one
        ldx ?i
        inx
        cpx #NSTR
        bne ?l
        ldx #NSTR                    ; the verdict sits past the static lines
        lda pm_seen
        bne ?v
        inx                          ; nothing there -> NONE
?v      lda #1
        jmp draw_one
?i      dta 0
.endp

;-----------------------------------------------------------------------------
; draw_options : all NOPT option lines, every call. Selected = colour 2
;   (yellow), the rest = colour 1 (white). Re-drawing recolours in place (see
;   the header) -- BSTENCIL never touches a pixel the glyph does not set.
;-----------------------------------------------------------------------------
.proc draw_options
        ldx #0
?l      stx ?i
        lda #1
        cpx set_sel
        bne ?w
        lda #2
?w      jsr draw_one
        ldx ?i
        inx
        cpx #NOPT
        bne ?l
        rts
?i      dta 0
.endp

;-----------------------------------------------------------------------------
; read_console : block until START, SELECT or OPTION, debounce on release.
;   Returns A = 1 (SELECT), 2 (START) or 3 (OPTION). START wins if several are
;   held; OPTION loses to both.
;-----------------------------------------------------------------------------
.proc read_console
?w      lda CONSOL
        and #$07
        cmp #$07
        beq ?w                       ; nothing pressed -> wait
        lda CONSOL
        and #$01
        beq ?start                   ; bit0 = 0 -> START
        lda CONSOL
        and #$02
        beq ?sel                     ; bit1 = 0 -> SELECT
        jsr ?rel
        lda #3
        rts
?start  jsr ?rel
        lda #2
        rts
?sel    jsr ?rel
        lda #1
        rts
?rel    lda CONSOL                   ; wait for release (debounce)
        and #$07
        cmp #$07
        bne ?rel
        rts
.endp

;-----------------------------------------------------------------------------
; set_settings_pal : VBXE palette #1 -- idx0 dark-blue bg, idx1 white, idx2
;   yellow. CSEL auto-increments on each CB write (like pal_init_black).
;-----------------------------------------------------------------------------
.proc set_settings_pal
        lda #1
        sta VBXE_PSEL
        lda #0
        sta VBXE_CSEL
        lda #$10
        sta VBXE_CR
        lda #$18
        sta VBXE_CG
        lda #$48
        sta VBXE_CB                  ; idx0 : dark-blue background
        lda #$F0
        sta VBXE_CR
        lda #$F0
        sta VBXE_CG
        lda #$F0
        sta VBXE_CB                  ; idx1 : white
        lda #$F0
        sta VBXE_CR
        lda #$D0
        sta VBXE_CG
        lda #$20
        sta VBXE_CB                  ; idx2 : yellow
        rts
.endp

;-----------------------------------------------------------------------------
; Data : selection state + strings (raw ASCII $20-$7F, $00-terminated -- matches
;   aw_strbytes; the font glyph index is byte-$20). Uppercase only (the AW font
;   is upper-case + digits). Kept outside the .procs (plain data labels).
;   Table order: the NOPT option lines first (set_sel indexes them straight),
;   then the statics, then the FOUND marker at index NSTR.
;-----------------------------------------------------------------------------
set_sel dta 0                        ; 0 = POKEY, 1..4 = COVOX $D280/$D500/$D600/$D700
pm_seen dta 0                        ; 1 = snd_probe found a covox at $D280

s_lo    dta <s_o0,<s_o1,<s_o2,<s_o3,<s_o4
        dta <s_title,<s_ver,<s_head,<s_k1,<s_k2,<s_k3
        dta <s_found,<s_none
s_hi    dta >s_o0,>s_o1,>s_o2,>s_o3,>s_o4
        dta >s_title,>s_ver,>s_head,>s_k1,>s_k2,>s_k3
        dta >s_found,>s_none
o_x     dta OPT_X,OPT_X,OPT_X,OPT_X,OPT_X
        dta 13,VER_X,14,OPT_X,OPT_X,OPT_X ; centred: x = (40 - length) / 2
        dta 30,30
o_y     dta OPT_Y0,OPT_Y0+16,OPT_Y0+32,OPT_Y0+48,OPT_Y0+64
        dta 24,36,52,160,172,184
        dta OPT_Y0+16,OPT_Y0+16
NSTR    = 11                         ; option + static lines; the $D280 verdict is
                                     ;   index NSTR (FOUND) or NSTR+1 (NONE)

s_o0    dta c'POKEY  4-BIT',0
s_o1    dta c'COVOX  D280   POKEYMAX',0
s_o2    dta c'COVOX  D500   CART/SDX!',0
s_o3    dta c'COVOX  D600   VBXE!',0
s_o4    dta c'COVOX  D700   P-COVOX',0
s_title dta c'ANOTHER WORLD',0

; s_ver + VER_X : the version line under the title -- "V<version>  <date time>",
; regenerated by build.ps1 on every build, so the screen always says which disk
; you are holding. The version NUMBER lives in build.ps1 ($version); VER_X is the
; centring column it computes for whatever length the line ends up.
        icl 'src/aw_version.inc'

s_head  dta c'SOUND OUTPUT',0
s_k1    dta c'SELECT = CHANGE',0
s_k2    dta c'OPTION = TEST SOUND',0
s_k3    dta c'START  = BEGIN',0
s_found dta c'FOUND',0
s_none  dta c'NONE',0
