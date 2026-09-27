;=============================================================================
; game_vm_input.asm  --  turn the player's controls into AW "input variables".
;
;   Once per frame the VM reads joystick 0 (PORTA) + the fire button (TRIG0) and
;   writes the hero-movement variables the bytecode polls: left/right, up/down,
;   jump, action and the direction bitmask (vm_update_input). vm_check_code watches
;   the keyboard for 'C' (open the access-code / password screen, part 16008) and
;   ESC (skip the intro, or leave the code screen back to where you came from),
;   turning a keypress into a pending part switch the scheduler honours next frame.
;
;   Part of the game_vm split -- icl'd from game_vm.asm in order; not standalone.
;=============================================================================

;=============================================================================
; vm_update_input : joystick 0 (PORTA + TRIG0) -> AW hero variables, once per
; frame (= game_sim.update_input).  Active-low switches: 0 bit = pressed.
;=============================================================================
.if 1
; Left/right and up/down are two independent 2-bit fields of PORTA (active low), so
; each axis value and mask part is a 4-entry table: the later direction wins, as in
; the branch chain (left over right, up over down). vm_s1/vm_s2 are no longer used.
vm_update_input
        lda #0
        sta ATRACT                  ; kill OS attract mode every frame (the joystick
        sta var_lo+V_ACTION         ;   never clears it -> colours cycle after ~9 min)
        sta var_hi+V_ACTION
        sta var_hi+V_MASK
        sta var_hi+V_ACT_MASK
        lda PORTA
        tax
        and #J_UP|J_DOWN
        tay                         ; Y = up/down bits
        txa
        lsr @
        lsr @
        and #[J_LEFT|J_RIGHT]>>2
        tax                         ; X = left/right bits
        lda ?axlo,x                 ; lr = -1 left / +1 right / 0
        sta var_lo+V_LEFT_RIGHT
        lda ?axhi,x
        sta var_hi+V_LEFT_RIGHT
        lda ?axlo,y                 ; ud = jd = -1 up / +1 down / 0
        sta var_lo+V_UP_DOWN
        sta var_lo+V_JUMP_DOWN
        lda ?axhi,y
        sta var_hi+V_UP_DOWN
        sta var_hi+V_JUMP_DOWN
        lda TRIG0
        lsr @                       ; C = 1 : fire NOT pressed
        lda ?mlr,x
        ora ?mud,y                  ; m = R1 L2 D4 U8 (lda / ora keep C)
        sta var_lo+V_MASK
        bcs ?nofire
        ora #$80                    ; var[ACT_MASK] = m | (action<<7)
        ldx #1
        stx var_lo+V_ACTION
?nofire sta var_lo+V_ACT_MASK
        rts
?axlo   dta $FF, $01, $FF, $00      ; index = bits (second, first) of the axis pair:
?axhi   dta $FF, $00, $FF, $00      ;   0 both, 1 only the +1 side, 2 only the -1 side
?mlr    dta 3, 1, 2, 0
?mud    dta 12, 4, 8, 0
        ert J_UP<>1
        ert J_DOWN<>2
        ert J_LEFT<>4
        ert J_RIGHT<>8
.else
vm_update_input
        lda #0                      ; clear every hero axis first
        sta ATRACT                  ; ...and kill OS attract mode every frame: the joystick
                                    ;   never clears it, else colors cycle/dim after ~9 min
        sta var_lo+V_UP_DOWN
        sta var_hi+V_UP_DOWN
        sta var_lo+V_JUMP_DOWN
        sta var_hi+V_JUMP_DOWN
        sta var_lo+V_LEFT_RIGHT
        sta var_hi+V_LEFT_RIGHT
        sta var_lo+V_ACTION
        sta var_hi+V_ACTION
        sta var_hi+V_MASK
        sta var_hi+V_ACT_MASK
        sta vm_s2                   ; m = 0 (direction mask)
        lda PORTA
        sta vm_s1                   ; joystick bits (active low)
        and #J_RIGHT                ; right -> lr = +1
        bne ?nr
        lda #1
        sta var_lo+V_LEFT_RIGHT
        lda vm_s2
        ora #1
        sta vm_s2
?nr     lda vm_s1
        and #J_LEFT                 ; left -> lr = -1
        bne ?nl
        lda #$FF
        sta var_lo+V_LEFT_RIGHT
        sta var_hi+V_LEFT_RIGHT
        lda vm_s2
        ora #2
        sta vm_s2
?nl     lda vm_s1
        and #J_DOWN                 ; down -> ud=+1, jd=+1
        bne ?nd
        lda #1
        sta var_lo+V_UP_DOWN
        sta var_lo+V_JUMP_DOWN
        lda vm_s2
        ora #4
        sta vm_s2
?nd     lda vm_s1
        and #J_UP                   ; up -> ud=-1, jd=-1  (AW jump)
        bne ?nu
        lda #$FF
        sta var_lo+V_UP_DOWN
        sta var_hi+V_UP_DOWN
        sta var_lo+V_JUMP_DOWN
        sta var_hi+V_JUMP_DOWN
        lda vm_s2
        ora #8
        sta vm_s2
?nu     lda vm_s2                   ; var[MASK] = m
        sta var_lo+V_MASK
        lda TRIG0                   ; fire -> action (run/kick, or shoot once armed)
        and #1
        bne ?nofire
        lda #1
        sta var_lo+V_ACTION
        lda vm_s2
        ora #$80                    ; var[ACT_MASK] = m | (action<<7)
        sta var_lo+V_ACT_MASK
        rts
?nofire lda vm_s2
        sta var_lo+V_ACT_MASK
        rts
.endif

;=============================================================================
; vm_check_code : press 'C' on the Atari keyboard -> switch to the access-code /
; password entry screen (part 16008), mirroring rawgl's _pi.code handler
; (script.cpp: if code key pressed and not already on the password / copy-
; protection part, _nextPart = kPartPassword). The grid is then navigated with
; the joystick. Ignored while already on part 16008 (idx 8) or 16000 (idx 0).
; The switch is honoured at the top of the NEXT vm_run_frame pass.
;=============================================================================
vm_check_code
        lda SKSTAT
        and #$04                    ; bit2 = 0 while a key is held; 1 = none
        bne ?none
        lda KBCODE
        and #$3F                    ; strip CTRL/SHIFT -> base key code
        cmp #KEY_ESC
        beq ?esc
        cmp #KEY_C
        bne ?none
        ldx dk_idx                  ; current part index (set by load_part)
        beq ?none                   ; 0 = copy-protection screen -> ignore
        cpx #GAME_NPARTS-1          ; 8 = password screen -> ignore (already there)
        beq ?none
        lda dk_idx                  ; remember where we came from (as a part NUMBER) so ESC
        clc                         ;   on the access-code screen can switch back to it
        adc #<GAME_FIRST_PART
        sta code_prev_lo
        lda #>GAME_FIRST_PART
        adc #0
        sta code_prev_hi
        lda #<16008                 ; request the part switch to 16008
        sta vm_next_lo
        lda #>16008
        sta vm_next_hi
        lda #1
        sta vm_switch
        rts
?esc    lda dk_idx                  ; ESC on the intro (idx 1) -> skip to water (16002)
        cmp #1
        bne ?esc2
        lda #<16002
        sta vm_next_lo
        lda #>16002
        sta vm_next_hi
        lda #1
        sta vm_switch
        rts
?esc2   lda dk_idx                  ; ESC on the access-code (idx 8) -> back to where we came from
        cmp #GAME_NPARTS-1
        bne ?none
        lda code_prev_lo
        sta vm_next_lo
        lda code_prev_hi
        sta vm_next_hi
        lda #1
        sta code_return            ; RESUME: restore saved threads, don't reset the scene
        sta vm_switch
?none   rts

