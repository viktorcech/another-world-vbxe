;=============================================================================
; game_vm.asm  -  the Another World bytecode VM in 6502 (GAME build, Phase 1).
;
;   The intro is a PRE-FLATTENED playlist (aw_replayer.asm walks a linear opcode
;   stream). The game runs the REAL AW bytecode: 64 cooperative threads, 256 16-bit
;   variables, the 27 opcodes, draw_bg / draw_sprite (video1 + the shared video2
;   bank), and the page model (cur1 draw / cur2 display / cur3 spare).
;
;   Bytecode lives in VRAM at PLAY_BASE ($060000) and is fetched through pl_byte
;   (the intro's running MEMAC-B reader) -- a JUMP just re-points pl_lo/pl_mid/pl_hi
;   because pl_byte recomputes the bank+window every byte. Draws call the shared
;   poly_draw; interleaving the bytecode bank and the poly bank is handled by the
;   memb_cur cache exactly as the intro already does.
;
;   This is the Phase-1 oracle target: it must match tools/game_atari.py (the
;   faithful 160-LR render) frame-for-frame on the water part.
;
;   Phase-1 stubs: input variables stay 0 (no joystick yet); op_memlist part switch
;   halts; op_sound / op_music only consume their operands.
;
;   FILE MAP -- the VM used to be one big file; it is now split into focused pieces
;   that THIS file pulls back together with `icl` at the bottom. The split is purely
;   organisational: MADS `icl` pastes each file inline, so the assembled bytes are
;   IDENTICAL to the old single file (verified by rebuild). This file keeps the
;   shared state (the variable / thread / page RAM map just below) and vm_init +
;   vm_reset_threads (they hold GAME_START_PART/POS, which tools/game_gui.py patches
;   by regex in THIS file). The rest lives in:
;     game_vm_input.asm     joystick + 'C'/ESC keys  -> AW input variables
;     game_vm_snapshot.asm  freeze/thaw a scene (the 'C' -> code screen -> ESC resume)
;     game_vm_sched.asm     the thread scheduler (run one frame, run one thread)
;     game_vm_fetch.asm     read bytecode bytes/words; move the program counter
;     game_vm_ops1.asm      opcodes 0x00-0x0A : maths, variables, branches
;     game_vm_ops2.asm      opcodes 0x0B-0x10 : screen pages, palette, display
;     game_vm_ops3.asm      opcodes 0x11-0x1A : thread kill, text, sound, loading
;     game_vm_draw.asm      decode a DRAW opcode -> poly_draw
;     game_vm_optab.asm     the 27-entry jump table the scheduler dispatches through
;=============================================================================

;-----------------------------------------------------------------------------
; VM state in RAM  (BASIC off => $A000-$BFFF is RAM; the game uses no text data).
;   var_lo/var_hi : 256 16-bit variables (split lo/hi pages for ,x indexing).
;   thread arrays : 64 entries each. tpc_hi==$FF marks an INACTIVE thread; treq
;                   ($FFFF none / $FFFE remove / else a PC); tpause / tpreq.
;-----------------------------------------------------------------------------
var_lo   = $B000                    ; 256
var_hi   = $B100                    ; 256
tpc_lo   = $B200                    ; 64  thread PC low
tpc_hi   = $B240                    ; 64  thread PC high ($FF = INACTIVE)
treq_lo  = $B280                    ; 64
treq_hi  = $B2C0                    ; 64  ($FFFF none, $FFFE remove, else new PC)
tpause   = $B300                    ; 64  (0 = run, 1 = paused)
tpreq    = $B340                    ; 64  ($FF none, else 0/1)
vstk_lo  = $B380                    ; 32  per-thread-run call stack
vstk_hi  = $B3A0                    ; 32

; persistent VM globals
vm_cur1  = $B3C0                    ; draw page
vm_cur2  = $B3C1                    ; displayed page
vm_cur3  = $B3C2                    ; spare page
vm_pend  = $B3C3                    ; deferred palette ($FF = none)
vm_hold  = $B3C4                    ; frame hold counter
poly_base_adj = $B3C5              ; 0 = video1, 8 = video2 (read by the poly fork)
vm_running = $B3C6                 ; 0 = VM halted (no active threads)
vm_switch  = $B3C7                 ; 1 = a part switch is pending
vm_lastpal = $B3C8                 ; last palette APPLIED at a blit ($FF = none yet);
                                   ;   snapshot/restore re-applies it on the ESC return
                                   ;   (16008 loaded its own green palette meanwhile)
vm_next_lo = $B3CF                 ; requested part number (16-bit)
vm_next_hi = $B3D0
pace_due   = $B3D1                 ; RTCLOK3 value at which the next frame is DUE (paced
                                   ; so frame-to-frame = VAR_PAUSE_SLICES vblanks, render
                                   ; absorbed -- mirrors rawgl pause = N/fps - elapsed)
pace_frac  = $B3D2                 ; NTSC speed-comp fractional accumulator (fifths of a
                                   ; vblank); carries hold*0.2 remainder so the avg is exact
code_prev_lo = $B3D3               ; part NUMBER we were on when 'C' opened the access-code
code_prev_hi = $B3D4               ;   screen, so ESC can switch back to it
code_return  = $B3D5               ; 1 = the pending switch is an ESC RETURN -> restore the
                                   ; saved thread state instead of resetting (resume, no reset)
; snapshot of the pre-access-code part's runtime state (taken before loading 16008, restored
; on ESC) so the scene RESUMES instead of restarting. Lives in free RAM $9600-$994x (below
; RAMB=$9C00; the $9000 data segment ends $95FF). 5 thread arrays x 64 + globals + the 256
; 16-bit VARIABLES: vars persist across part switches by AW design, but 16008's scripts
; WRITE them (cursor/keys/...), and a resumed scene reading clobbered vars drew the hero
; as garbage at the top row -- so the ESC-resume must bring the whole var file back too.
SNAP        = $9600                ; tpc_lo[64] | tpc_hi[64] | tpause[64] | treq_lo[64] | treq_hi[64]
SNAP_G      = SNAP+320             ; vm_cur1, vm_cur2, vm_cur3, vm_pend, vm_hold, vm_lastpal
SNAP_V      = SNAP+328             ; var_lo[256] | var_hi[256]  (ends $9947)
; the four LR framebuffer pages are snapshot too -- into VRAM holes that part 16008
; provably never touches (its v1 = 5120 B, code = 4352 B, no video2, no SFX; guarded
; by tools/check_layout.py). load_part(prev) restreams poly/code/v2/sfx AFTER the
; restore, so clobbering the prev part's sample banks ($1E/$1F) is fine.
PSAV0       = $052000              ; page 0 -> poly region hole (16008 v1 ends $0513FF)
PSAV1       = $062000              ; page 1 -> code region hole (16008 code ends $0610FF)
PSAV2       = $070000              ; page 2 -> video2 region (16008 has none)
PSAV3       = $078000              ; page 3 -> video2 upper / prev part's SFX banks

; hardware joystick 0 (PIA PORTA + trigger), read directly each frame
PORTA    = $D300                    ; joystick 0 in bits 0-3, active LOW (0 = pressed)
TRIG0    = $D010                    ; fire button, bit0 (0 = pressed)
KBCODE   = $D209                    ; last keyboard scan code (bits0-5 key, 6=CTRL, 7=SHIFT)
SKSTAT   = $D20F                    ; bit2 = 0 while a key is currently held down
ATRACT   = $004D                    ; OS attract-mode timer: OS bumps it every VBI and
                                    ;   only the KEYBOARD IRQ clears it. Joystick-only play
                                    ;   never touches it -> after ~9 min the OS cycles/dims
                                    ;   the colors. We zero it each frame (see vm_update_input).
KEY_C    = $12                      ; Atari hw keycode for 'C' (the AW "load code" key)
KEY_ESC  = $1C                      ; Atari hw keycode for ESC (skip the intro)
J_UP     = $01                      ; PORTA bit0 = stick forward (up)  -> AW jump
J_DOWN   = $02                      ; bit1 = back (down)
J_LEFT   = $04                      ; bit2 = left
J_RIGHT  = $08                      ; bit3 = right

; AW hero input variables (rawgl script.h)
V_UP_DOWN   = $E5                   ; -1 up / +1 down
V_ACTION    = $FA                   ; 1 = fire held
V_JUMP_DOWN = $FB                   ; -1 up / +1 down (jump axis)
V_LEFT_RIGHT= $FC                   ; -1 left / +1 right
V_MASK      = $FD                   ; direction bitmask (R1 L2 D4 U8)
V_ACT_MASK  = $FE                   ; mask | (action<<7)

; pl_byte's cached MEMAC-B bank (aw2.txt running-pointer fetch). Reuses the intro's
; op_drawtext char-ptr ZP ($B3) -- the game renders no text, so it is free here.
pl_bank  = $B3

; zero-page scratch (the $B8-$BD gap, free per aw_equates.inc)
vm_t     = $B8                      ; current thread index
vm_ssp   = $B9                      ; call-stack pointer
vm_goto  = $BA                      ; 1 = thread slice ended
vm_rem   = $BB                      ; 1 = thread removed itself
vm_s1    = $BC                      ; word-fetch low / general scratch
vm_s2    = $BD                      ; word-fetch high / general scratch

; transient per-opcode scratch (RAMB free gap +93..+127, below the vertex buffers;
; not touched by the raster, so it survives a draw). NOTE: poly_bcb_h lives at +102
; (aw_equates.inc) -- keep these vm_* vars below it (+93..+101). +94 is FREE
; (the old vm_jmp dispatch cell -> replaced by SMC at vm_disp; see vm_fetch).
req_any  = RAMB+93                  ; 1 = treq/tpreq posted since the last apply scan
                                    ;   (lets vm_run_frame skip the 64-thread scan)
vm_op    = RAMB+95                  ; saved draw opcode / scratch
vm_d     = RAMB+96                  ; dest var index / scratch
vm_sub   = RAMB+97                  ; condjmp sub byte
vm_b2lo  = RAMB+98                  ; condjmp operand
vm_b2hi  = RAMB+99
vm_dstlo = RAMB+100                 ; condjmp destination PC
vm_dsthi = RAMB+101
cp_vs    = RAMB+104                 ; copy_page_vs: |VAR_SCROLL_Y| magnitude (rows)
cp_vd    = RAMB+105                 ; copy_page_vs: 1 = scroll down (offset DST), 0 = up

;=============================================================================
; vm_init : reset variables, threads, page state.  (Resources are already in VRAM.)
;=============================================================================
vm_init
        ldx #0                      ; zero all 256 variables
        lda #0
?vz     sta var_lo,x
        sta var_hi,x
        inx
        bne ?vz
        ; AW startup variable markers (rawgl BYPASS_PROTECTION + boot defaults)
        lda #$81
        sta var_lo+$54
        lda #$10
        sta var_lo+$BC
        lda #$80
        sta var_lo+$C6
        lda #$A0                    ; var 0xF2 = 4000 = $0FA0 (required, else dead path)
        sta var_lo+$F2
        lda #$0F
        sta var_hi+$F2
        lda #33
        sta var_lo+$DC
        lda #20
        sta var_lo+$E4
        ; page state : cur1=2 (draw), cur2=2 (display), cur3=1 (spare)
        lda #2
        sta vm_cur1
        sta vm_cur2
        lda #1
        sta vm_cur3
        lda #$FF
        sta vm_pend
        sta vm_lastpal              ; no palette applied yet
        lda #1
        sta vm_running
        lda #0
        sta vm_switch
        sta code_return             ; 0 = boot load is a normal load -> show the LOADING screen
; GAME_START_PART : which part the game boots into (default 16002 = water gameplay).
; The game GUI's "TEST IN ALTIRRA" patches this line to boot straight into a chosen
; scene (water/jail/arene/password/...), then rebuilds the ATR -- like woll3d's menu.
GAME_START_PART = 16001        ; boot into the INTRO (part 16001); the VM plays it and
                               ; auto-switches to water (16002) at its end. ESC skips it.
        ldx #GAME_START_PART-GAME_FIRST_PART  ; load the start part from the ATR
        jsr load_part
        jsr vm_reset_threads
; GAME_START_POS : the AW VAR(0) checkpoint within the start part (0 = the part's
; natural start). restartAt(part,pos) on the password screen sets VAR(0)=pos before
; the part's thread 0 runs; we do the same at boot so the GUI's "TEST IN ALTIRRA"
; lands exactly where its preview shows. pos < 256, so the hi byte is always 0.
; (vm_init zeroed all vars above; this runs after, so it survives into thread 0.)
GAME_START_POS = 0
        lda #<GAME_START_POS
        sta var_lo                  ; var_lo[0] = VAR(0) low byte
        lda #>GAME_START_POS
        sta var_hi                  ; var_hi[0] = VAR(0) high byte
        lda vm_cur1                 ; draw page = cur1
        sta cur_draw
        jsr set_cbase_cur
        lda RTCLOK3                 ; pacing: first frame is due now
        sta pace_due
        lda #0
        sta pace_frac               ; NTSC speed-comp accumulator starts empty
        rts

;=============================================================================
; vm_reset_threads : all threads INACTIVE except thread 0 (pc 0). Variables and
; page state are NOT touched -- they persist across a part switch (= load_part).
;=============================================================================
vm_reset_threads
        ldx #0
?tz     lda #$FF
        sta tpc_hi,x                ; INACTIVE
        sta treq_lo,x
        sta treq_hi,x               ; NO_REQ
        sta tpreq,x
        lda #0
        sta tpc_lo,x
        sta tpause,x
        inx
        cpx #64
        bne ?tz
        lda #0                      ; thread 0 active at pc 0
        sta tpc_lo
        sta tpc_hi
        sta req_any                 ; all requests are NO_REQ -> nothing pending
        rts

;=============================================================================
; The rest of the VM lives in focused files, icl'd HERE IN ORDER. MADS icl is a
; textual inline include, so this assembles byte-identically to the old single
; file. ORDER MATTERS: game_vm_fetch.asm defines the m_vm_w macro that the opcode
; and draw files expand, so it must precede them.
;   (GAME_START_PART / GAME_START_POS stay ABOVE in vm_init -- tools/game_gui.py
;    patches them by regex in THIS file, so they must not move out.)
;=============================================================================
        icl 'src_game/game_vm_input.asm'     ; vm_update_input, vm_check_code
        icl 'src_game/game_vm_snapshot.asm'  ; snapshot/restore_state, pages_xfer
        icl 'src_game/game_vm_sched.asm'     ; vm_run_frame, vm_run_thread
        icl 'src_game/game_vm_fetch.asm'     ; m_vm_w macro, vm_w, vm_setpc, vm_save_pc, vm_page
        icl 'src_game/game_vm_ops1.asm'      ; opcodes 0x00-0x0A (var / arith / flow)
        icl 'src_game/game_vm_ops2.asm'      ; opcodes 0x0B-0x10 (page / palette / display) + copy_page_vs
        icl 'src_game/game_vm_ops3.asm'      ; opcodes 0x11-0x1A (remove / bitwise / sound / resource)
        icl 'src_game/game_vm_draw.asm'      ; draw_bg, draw_sprite, do_draw
        icl 'src_game/game_vm_optab.asm'     ; vm_optab jump table
