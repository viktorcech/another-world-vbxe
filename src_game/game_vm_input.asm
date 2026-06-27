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

