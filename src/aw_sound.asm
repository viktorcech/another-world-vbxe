;=============================================================================
; aw_sound.asm - INTRO audio via a POKEY sample player (doom2d-derived).
;
;   TWO independent 4-bit voices on POKEY volume-only channels, one Timer 1
;   IRQ via the VIMIRQ direct hook (~3995 Hz):
;     MUSIC (AUDC2) : one FREE-RUNNING pre-rendered stream of AW music #7
;       (tools/render_intro_audio.py, MUSIC ONLY -- no SFX baked in). Started
;       by playlist opcode 0x09, ends by its own 24-bit byte count. Free-run
;       is the drift fix: the old build pre-mixed SFX INTO the music stream
;       and the baked SFX drifted against the deadline-paced visuals; a pure
;       music bed needs no sync, so nothing can drift out of place. The voice
;       advances every OTHER tick (~1998 Hz; mus_hold toggle): the full piece
;       is ~117 s incl. one op_music tempo change (the old build cut it at
;       45 s by misreading that change as a stop), and 117 s @ 3995 Hz =
;       234 KB would not fit VRAM -- @ half rate it is ~118 KB in the 8 free
;       banks $02,$03,$06,$07,$0A,$0B,$11,$12 (the page-0..2 gaps + the
;       orphaned COVOX banks; NOT contiguous -> the bank advance walks
;       mus_banks). See memory intro-sound-scope.
;     SFX (AUDC4) : ONE sound at a time (latest wins), playlist opcode 0x08
;       = variant idx (1) + vol (1). PITCH is baked offline: one blob variant
;       per (resource,freq) combo (gen_intro_sfx.py), truncated to its max
;       audible window. VOLUME is applied at run time: snd_play copies one of
;       16 voltab curves to snd_vt ($0600, page-aligned; runtime-free page --
;       the INI stubs there are load-time only) and the IRQ routes every
;       output nibble through it via an SMC operand low byte (+8 cyc, no
;       extra register). The blob spans the NON-contiguous banks in sfx_blist
;       ($0E,$0F,$13,$1F) -- window crossings walk the list like the music.
;   Both voices read VRAM through the MEMAC-B window; the shared IRQ tail
;   restores the bank to memb_cur (poly_fetch/pl_byte are cache-first +
;   only-on-change, so register==memb_cur is their invariant -> safe). The
;   timer is disabled only when BOTH voices are idle.
;
;   PERF: the IRQ fires at the tick rate (~4 kHz). Per-IRQ cost keeps the old
;   squeezed shape per voice (A-only save; X is touched only on the music
;   bank-list step, every 16 KB ~ 16 s, saved via memory): music alone ~88 cyc
;   avg (~20% CPU; work tick ~108, hold tick ~68), music+SFX ~128 cyc (~29%),
;   SFX alone ~100 cyc (~23%).
;=============================================================================
; POKEY registers
AUDF1   = $D200
AUDC1   = $D201
AUDC2   = $D203
AUDC4   = $D207
AUDCTL  = $D208
STIMER  = $D209
IRQEN   = $D20E                      ; write = IRQ enable; read = IRQ status
SKCTL   = $D20F
POKMSK  = $0010
VIMIRQ  = $0216

        icl 'src/aw_music_len.inc'   ; MUSIC_LEN (render_intro_audio.py)
        ert MUSIC_LEN>[8*$4000]      ; must fit the 8 free banks (mus_banks)
MUS_NEG = $1000000-MUSIC_LEN         ; negated 24-bit count: the IRQ counts UP

; IRQ-hot state in ZERO PAGE: $B8/$B9 were the old snd_win pointer (replaced by
; the SMC read operand); $AD (poly_hi) is free since the wave-2 fetch rework;
; $BC/$BD were unassigned.
snd_active = $B8                     ; SFX MERGED state: 0 = off ; 1 = phase 0
                                     ;   next (fetch byte, hi nibble) ; 2 =
                                     ;   phase 1 next (lo nibble + advance)
zsnd_cur   = $B9                     ; current SFX byte (lo nibble for phase 1)
zsnd_bank  = $AD                     ; current SFX MEMAC-B bank ($80|bank, contiguous)
zmus_st    = $BC                     ; MUSIC state, same encoding as snd_active
zmus_bank  = $BD                     ; current MUSIC MEMAC-B bank ($80|bank, from mus_banks)

snd_vt     = $0600                   ; 16-byte volume curve (page-aligned: the IRQ
                                     ;   indexes it by nibble via an SMC low byte)

snd_rem      dta a(0)                ; (2) NEGATED SFX bytes remaining (-len): the
                                     ;   IRQ counts UP (`inc` = 9 cyc common case);
                                     ;   $0000 = sample done
snd_bidx     dta 0                   ; index into sfx_blist of the current SFX bank
snd_old_iir  dta a(0)
mus_cur      dta 0                   ; current MUSIC byte (lo nibble for phase 1)
mus_rem      dta 0,0,0               ; (3) NEGATED music bytes remaining (24-bit:
                                     ;   the stream is ~91 KB > 64 KB)
mus_bidx     dta 0                   ; index into mus_banks of the current bank
mus_savx     dta 0                   ; X save for the rare bank-list step
mus_hold     dta 0                   ; half-rate toggle: process music every OTHER
                                     ;   tick (0 = hold the current nibble)
mus_banks    dta $82,$83,$86,$87,$8A,$8B,$91,$92
                                     ; $80|bank : the VRAM gaps behind pages 0-2
                                     ;   (32 KB each) + the orphaned COVOX banks
                                     ;   $11,$12 -- NOT contiguous

;-----------------------------------------------------------------------------
snd_init
        lda #0
        sta SKCTL
        nop
        nop
        lda #3
        sta SKCTL
        lda #0
        sta AUDCTL
        sta AUDC1
        sta AUDC2
        sta AUDC4
        lda #15
        sta AUDF1                    ; ~3995 Hz
        lda #0
        sta snd_active
        sta zmus_st
        sei
        lda VIMIRQ
        sta snd_old_iir
        lda VIMIRQ+1
        sta snd_old_iir+1
        lda #<snd_irq
        sta VIMIRQ
        lda #>snd_irq
        sta VIMIRQ+1
        cli
        rts

;-----------------------------------------------------------------------------
; snd_stop : silence both voices and unhook the Timer 1 IRQ (VIMIRQ back to the
;   OS handler). MUST run before the intro hands control to the boot loader
;   (ESC skip / intro_done chain): SIO relies on the serial IRQs running through
;   VIMIRQ, and the loader overwrites the RAM snd_irq lives in.
;-----------------------------------------------------------------------------
snd_stop
        sei
        lda #0
        sta AUDC2
        sta AUDC4
        lda POKMSK
        and #$FE                     ; Timer 1 off
        sta POKMSK
        sta IRQEN
        lda snd_old_iir
        sta VIMIRQ
        lda snd_old_iir+1
        sta VIMIRQ+1
        cli
        rts

;-----------------------------------------------------------------------------
; snd_play : X = variant index (0..SFX_COUNT-1), A = volume 0..63.
;   Copies the matching voltab curve to snd_vt, points at the VRAM sample,
;   starts it. Clobbers A,Y (mainline-only; pl_byte clobbers Y anyway).
;-----------------------------------------------------------------------------
snd_play
        cpx #SFX_COUNT
        bcs ?skip
        and #$3C                       ; voltab row = vol>>2 ; row byte offset =
        asl @                          ;   (vol>>2)*16 = (vol&$3C)<<2
        asl @
        clc
        adc #<voltab
        sta ?vs+1                      ; -> the copy loop's SMC base
        lda #>voltab
        adc #0
        sta ?vs+2
        ldy #15
?vs     lda voltab,y                   ; operand patched above (voltab row)
        sta snd_vt,y
        dey
        bpl ?vs
        lda sfx_winlo,x                ; read addr -> the SMC load operand directly
        sta snd_irq.snd_rd+1
        lda sfx_winhi,x
        sta snd_irq.snd_rd+2
        lda sfx_bank,x
        sta zsnd_bank
        lda sfx_bidx,x                 ; the variant's start bank within sfx_blist
        sta snd_bidx
        sec                            ; snd_rem = -len (the IRQ counts UP to $0000)
        lda #0
        sbc sfx_lenlo,x
        sta snd_rem
        lda #0
        sbc sfx_lenhi,x
        sta snd_rem+1
        sei
        lda #1
        sta snd_active                 ; state 1 = phase 0 next
        lda POKMSK
        ora #$01
        sta POKMSK
        sta IRQEN
        sta STIMER
        cli
?skip   rts

;-----------------------------------------------------------------------------
; mus_play : start the free-running music stream (playlist opcode 0x09).
;   Idempotent enough for this intro: the playlist emits it exactly once.
;-----------------------------------------------------------------------------
mus_play
        lda #$00                       ; stream starts at the window base ($4000)
        sta snd_irq.mus_rd+1
        lda #$40
        sta snd_irq.mus_rd+2
        lda mus_banks
        sta zmus_bank
        lda #0
        sta mus_bidx
        sta mus_hold                   ; first tick processes (the toggle flips to 1)
        lda #<MUS_NEG                  ; mus_rem = -MUSIC_LEN (24-bit, counts UP)
        sta mus_rem
        lda #[[MUS_NEG>>8]&$FF]
        sta mus_rem+1
        lda #[[MUS_NEG>>16]&$FF]
        sta mus_rem+2
        sei
        lda #1
        sta zmus_st                    ; state 1 = phase 0 next
        lda POKMSK
        ora #$01
        sta POKMSK
        sta IRQEN
        sta STIMER                     ; (re)start Timer 1; if an SFX is mid-play
        cli                            ;   this shifts its cadence by <1 period
        rts

;-----------------------------------------------------------------------------
; snd_irq : Timer 1 IRQ (VIMIRQ hook; chains non-Timer-1 IRQs). Preserves A
;   (X only on the rare music bank-list step, saved via memory). Each tick
;   advances BOTH voices; their nibble phases are independent. All exits go
;   through ?tail, which restores MEMAC-B to memb_cur (a redundant write when
;   nothing was read -- cheaper than tracking it).
;-----------------------------------------------------------------------------
.proc snd_irq
        pha
        lda IRQEN                    ; bit0 = 0 -> Timer 1 pending (ours)
        and #$01
        beq ?ours
        pla
        jmp (snd_old_iir)            ; not ours -> chain (keyboard/break to the OS)
?ours   lda POKMSK                   ; acknowledge + re-arm Timer 1
        and #$FE
        sta IRQEN
        lda POKMSK
        sta IRQEN

        ; ---- voice 1 : MUSIC on AUDC2 ----
        lda zmus_st
        bne ?m_go
        lda snd_active               ; music off:
        beq ?off2                    ;   both off -> stray after stop -> disable
        jmp ?s_go                    ;   SFX-only tick
?off2   jmp ?off
?m_go   lda mus_hold                 ; half-rate: advance the voice every OTHER
        eor #1                       ;   tick; on the hold tick AUDC2 keeps the
        sta mus_hold                 ;   current nibble
        beq ?sfx
        lda zmus_st
        cmp #2
        beq ?m_lo
        ; --- state 1 / phase 0 : read the VRAM byte, output the HI nibble ---
        lda zmus_bank
        sta VBXE_MEMAC_B
mus_rd  lda $4000                    ; operand = byte ptr (mus_play / ?madv patch it)
        sta mus_cur                  ; lo nibble parked for phase 1
        lsr @
        lsr @
        lsr @
        lsr @
        ora #$10
        sta AUDC2
        lda #2
        sta zmus_st                  ; state 2 = phase 1 next
        bne ?sfx                     ; always
?m_lo   ; --- state 2 / phase 1 : output the LO nibble, count, advance ---
        lda mus_cur
        and #$0F
        ora #$10
        sta AUDC2
        lda #1
        sta zmus_st                  ; state 1 = phase 0 next
        inc mus_rem                  ; rem++ toward $000000 (stored negated, 24-bit)
        bne ?madv                    ; common case: 9 cyc total
        inc mus_rem+1
        bne ?madv
        inc mus_rem+2
        bne ?madv
        lda #0                       ; stream done -> music off; the timer stays
        sta zmus_st                  ;   if an SFX is still running (checked below)
        sta AUDC2
        lda snd_active
        bne ?sfx
        jmp ?off                     ; nothing left -> disable the timer
?madv   inc mus_rd+1                 ; advance the read operand to the next byte
        bne ?sfx
        inc mus_rd+2
        lda mus_rd+2
        cmp #$80                     ; crossed the 16 KB window -> next bank from
        bne ?sfx                     ;   the LIST (the gap banks are NOT contiguous)
        lda #$40
        sta mus_rd+2
        stx mus_savx                 ; rare (every 16 KB ~ 16 s): X via memory
        inc mus_bidx
        ldx mus_bidx
        lda mus_banks,x
        sta zmus_bank
        ldx mus_savx

        ; ---- voice 2 : SFX on AUDC4 ----
?sfx    lda snd_active
        beq ?tail                    ; no SFX; music runs -> the timer stays armed
?s_go   cmp #2
        beq ?s_lo
        ; --- state 1 / phase 0 : read the VRAM byte, output the HI nibble ---
        lda zsnd_bank
        sta VBXE_MEMAC_B
snd_rd  lda $4000                    ; operand = byte ptr (snd_play / phase 1 patch it)
        sta zsnd_cur                 ; lo nibble parked for phase 1
        lsr @
        lsr @
        lsr @
        lsr @
        sta ?vh+1                    ; nibble -> snd_vt index (page-aligned, SMC:
?vh     lda snd_vt                   ;   operand low byte = the nibble)
        sta AUDC4                    ; curve entries are pre-ORed with $10
        lda #2
        sta snd_active               ; state 2 = phase 1 next
        bne ?tail                    ; always
?s_lo   ; --- state 2 / phase 1 : output the LO nibble, count, advance ---
        lda zsnd_cur
        and #$0F
        sta ?vl+1                    ; volume curve, as in phase 0
?vl     lda snd_vt
        sta AUDC4
        lda #1
        sta snd_active               ; state 1 = phase 0 next
        inc snd_rem                  ; rem++ toward $0000 (stored negated)
        bne ?adv                     ; common case: 9 cyc total
        inc snd_rem+1
        bne ?adv
        lda #0                       ; sample done -> SFX off; keep the timer if
        sta snd_active               ;   the music is still streaming
        sta AUDC4
        lda zmus_st
        bne ?tail
        jmp ?off                     ; nothing left -> disable the timer
?adv    inc snd_rd+1                 ; advance the read operand to the next byte
        bne ?tail
        inc snd_rd+2
        lda snd_rd+2
        cmp #$80                     ; crossed the 16 KB window -> next bank from
        bne ?tail                    ;   sfx_blist (the blob is NOT contiguous)
        lda #$40
        sta snd_rd+2
        stx mus_savx                 ; rare: X via memory (shared save -- the IRQ
        inc snd_bidx                 ;   is single-threaded)
        ldx snd_bidx
        lda sfx_blist,x
        sta zsnd_bank
        ldx mus_savx
?tail   lda memb_cur                 ; restore the poly/playlist bank (a no-op
        sta VBXE_MEMAC_B             ;   write when nothing was read this tick)
        pla
        rti
?off    lda #0                       ; both voices idle -> mute + disable Timer 1
        sta AUDC2
        sta AUDC4
        lda POKMSK
        and #$FE
        sta POKMSK
        sta IRQEN
        pla
        rti
.endp

;-----------------------------------------------------------------------------
        icl 'src/aw_sfx_tables.inc'           ; SFX_COUNT, sfx_bank/winlo/winhi/lenlo/lenhi
