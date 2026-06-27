;=============================================================================
; aw_sound.asm - INTRO sound effects via a POKEY sample player (doom2d-derived).
;
;   4-bit packed nibbles (hi first), POKEY VOLUME-ONLY on AUDC4, Timer 1 IRQ via
;   the VIMIRQ direct hook (~3995 Hz), ONE sound at a time (latest wins). Samples
;   live in VBXE VRAM (banks $0E-$0F, $038000..) and are read in the IRQ through
;   the MEMAC-B window, then the bank is restored to memb_cur (poly_fetch/pl_byte
;   are cache-first + only-on-change, so register==memb_cur is their invariant ->
;   safe). The replayer fires `ldx #idx / jsr snd_play` on playlist opcode 0x08.
;   SCOPE: sound effects only -- music was tried and removed (sync drift). See
;   memory intro-sound-scope (tools/render_intro_audio.py keeps the offline music).
;
;   PERF (2026-06-10, port of the game's snd_irq rewrite): the IRQ fires at the
;   sample rate (~4 kHz = ~29% CPU while a sound plays), so the handler was
;   squeezed ~20% lossless: A-only save (the intro blob is CONTIGUOUS, so the
;   bank advance is a plain inc -- X is never touched), active+phase merged into
;   ONE ZP state byte, the read pointer is the SELF-MODIFIED operand of the
;   phase-0 load (frees the old snd_win ZP pair for the hot vars), the byte
;   count is stored NEGATED so the per-byte countdown is `inc/bne` (9 cyc vs the
;   old 3-way end-pointer compare ~20), and AUDC4 is written BEFORE the bank
;   restore (earlier output = less jitter). ~127 -> ~100 cyc per IRQ.
;=============================================================================
; POKEY registers
AUDF1   = $D200
AUDC1   = $D201
AUDC4   = $D207
AUDCTL  = $D208
STIMER  = $D209
IRQEN   = $D20E                      ; write = IRQ enable; read = IRQ status
SKCTL   = $D20F
POKMSK  = $0010
VIMIRQ  = $0216

; IRQ-hot state in ZERO PAGE: $B8/$B9 were the old snd_win pointer (replaced by
; the SMC read operand); $AD (poly_hi) is free since the wave-2 fetch rework.
snd_active = $B8                     ; MERGED state: 0 = off ; 1 = phase 0 next
                                     ;   (fetch byte, hi nibble) ; 2 = phase 1
                                     ;   next (lo nibble + advance)
zsnd_cur   = $B9                     ; current sample byte (lo nibble for phase 1)
zsnd_bank  = $AD                     ; current MEMAC-B bank ($80|bank, contiguous)

snd_rem      dta a(0)                ; (2) NEGATED bytes remaining (-len): the IRQ
                                     ;   counts UP (`inc` = 9 cyc common case);
                                     ;   $0000 = sample done
snd_old_iir  dta a(0)

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
        sta AUDC4
        lda #15
        sta AUDF1                    ; ~3995 Hz
        lda #0
        sta snd_active
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
; snd_play : X = sfx index (0..SFX_COUNT-1). Point at the VRAM sample, start it.
;-----------------------------------------------------------------------------
snd_play
        cpx #SFX_COUNT
        bcs ?skip
        lda sfx_winlo,x                ; read addr -> the SMC load operand directly
        sta snd_irq.snd_rd+1
        lda sfx_winhi,x
        sta snd_irq.snd_rd+2
        lda sfx_bank,x
        sta zsnd_bank
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
; snd_irq : Timer 1 IRQ (VIMIRQ hook; chains non-Timer-1 IRQs). Preserves A
;   (only A is used -- the contiguous blob needs no indexed bank list).
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
        lda snd_active
        beq ?off                     ; 0 = stray after stop -> mute + disable
        cmp #2
        beq ?lo
        ; --- state 1 / phase 0 : read the VRAM byte, output the HI nibble ---
        lda zsnd_bank
        sta VBXE_MEMAC_B
snd_rd  lda $4000                    ; operand = byte ptr (snd_play / phase 1 patch it)
        sta zsnd_cur                 ; lo nibble parked for phase 1
        lsr @                        ; hi nibble -> AUDC4 ASAP (shift in A: nothing
        lsr @                        ;   reads VRAM here; restore AFTER the output)
        lsr @
        lsr @
        ora #$10
        sta AUDC4
        lda memb_cur                 ; restore the poly/playlist bank
        sta VBXE_MEMAC_B
        lda #2
        sta snd_active               ; state 2 = phase 1 next
        pla
        rti
?lo     ; --- state 2 / phase 1 : output the LO nibble, count, advance ---
        lda zsnd_cur
        and #$0F
        ora #$10
        sta AUDC4
        lda #1
        sta snd_active               ; state 1 = phase 0 next
        inc snd_rem                  ; rem++ toward $0000 (stored negated)
        bne ?adv                     ; common case: 9 cyc total
        inc snd_rem+1
        bne ?adv
        beq ?stop                    ; rem hit $0000 -> sample done
?adv    inc snd_rd+1                 ; advance the read operand to the next byte
        beq ?page                    ; rare: page cross
        pla
        rti
?page   inc snd_rd+2
        lda snd_rd+2
        cmp #$80                     ; crossed the 16 KB window -> next bank
        bne ?pgok                    ;   (the intro blob is CONTIGUOUS: just inc)
        lda #$40
        sta snd_rd+2
        inc zsnd_bank
?pgok   pla
        rti
?stop   lda #0
        sta snd_active
        sta AUDC4
        lda POKMSK
        and #$FE
        sta POKMSK
        sta IRQEN
        pla
        rti
?off    lda #0
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
