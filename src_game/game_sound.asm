;=============================================================================
; game_sound.asm  -  GAME sound effects via the POKEY sample player.
;
;   The real VM calls op_sound live, so SFX are inherently in sync with gameplay.
;   Each part's FULL sound set (the type-0 resources it op_memlists at part start)
;   is loaded with the part into 7 free, NON-contiguous VRAM banks (snd_blist =
;   $0E,$0F,$11,$12,$13,$1E,$1F). 4-bit POKEY nibbles at NATIVE length; op_sound
;   sets AUDF1 from the freq byte (snd_audf) for correct pitch. ONE sound at a time
;   (latest wins), volume-only on AUDC4, Timer 1 IRQ via the VIMIRQ direct hook.
;
;   A sound is located by a directory entry (bank-list index + window + byte
;   length); the player counts bytes to 0 and steps to the next snd_blist bank
;   on a 16 KB window crossing. The IRQ reads from VRAM through the MEMAC-B
;   window then restores memb_cur (poly_fetch/pl_byte invariant). The read ptr
;   is the SELF-MODIFIED operand of the phase-0 load. Tables: game_atr.inc.
;
;   PERF (2026-06-10): the IRQ fires at the sample rate (3.4-21.3 kHz measured
;   across the parts!), so the handler was squeezed ~20% lossless: A-only
;   save (X only in the rare bank-advance path), active+phase merged into ONE
;   ZP state byte, hot vars in ZP, snd_rem stored NEGATED so the countdown is
;   `inc/bne` (9 cyc) instead of dec+or-test (22), AUDC4 written before the
;   bank restore (earlier output = less jitter). ~125 -> ~100 cyc per IRQ.
;=============================================================================
AUDF1   = $D200
AUDC1   = $D201
AUDC4   = $D207
AUDCTL  = $D208
STIMER  = $D209
IRQEN   = $D20E
SKCTL   = $D20F
POKMSK  = $0010
VIMIRQ  = $0216

; --- IRQ-hot state in ZERO PAGE. $AB/$AD/$B4 are free in the GAME build: the
; intro symbols that own them (pl_bnk / poly_hi / txt_ptr+1) are not referenced
; by the game fork (game uses pl_bank=$B3, gtxt_ptr=$C0).
snd_active = $AB                     ; MERGED state: 0 = off ; 1 = phase 0 next
                                     ;   (fetch byte, hi nibble) ; 2 = phase 1
                                     ;   next (lo nibble + advance). diskio still
                                     ;   writes 0 here to silence before a load.
zsnd_cur   = $AD                     ; current sample byte (lo nibble for phase 1)
zsnd_bank  = $B4                     ; current MEMAC-B bank (= snd_blist[snd_blidx])

snd_blidx     dta 0                  ; index into snd_blist
snd_rem       dta a(0)               ; (2) NEGATED bytes remaining (-len): the IRQ
                                     ;   counts UP (`inc` = 9 cyc common case);
                                     ;   $0000 = sample done
snd_xsave     dta 0                  ; X save for the rare bank-advance path
snd_old_iir   dta a(0)
cur_dir_start dta 0                  ; current part's slice of the resId directory
cur_dir_cnt   dta 0
snd_req_freq  dta 0                  ; op_sound's freq byte (-> AUDF1)

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
        sta AUDF1
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
; snd_play : X = directory index (resId resolved + AUDF1 set by op_sound).
;-----------------------------------------------------------------------------
snd_play
        lda snd_dir_winlo,x            ; set the IRQ read addr DIRECTLY in the load operand
        sta snd_irq.snd_rd+1           ;   (snd_rd is the self-modified `lda $4000` in phase 0;
        lda snd_dir_winhi,x            ;    phase 1 increments snd_rd+1/+2 -> no per-IRQ copy.
        sta snd_irq.snd_rd+2           ;    .proc scopes the label -> qualify it)
        sec                            ; snd_rem = -len (the IRQ counts UP to $0000)
        lda #0
        sbc snd_dir_lenlo,x
        sta snd_rem
        lda #0
        sbc snd_dir_lenhi,x
        sta snd_rem+1
        lda snd_dir_blidx,x
        sta snd_blidx
        tay
        lda snd_blist,y                ; bank = snd_blist[blidx]
        sta zsnd_bank
        sei
        lda #1
        sta snd_active                 ; state 1 = phase 0 next
        lda POKMSK
        ora #$01
        sta POKMSK
        sta IRQEN
        sta STIMER
        cli
        rts

;-----------------------------------------------------------------------------
; snd_irq : Timer 1 IRQ (VIMIRQ hook; chains non-Timer-1). Preserves A; X only
;   in the rare bank-advance path (snd_xsave) -- the hot path never touches X/Y.
;   .proc scopes the ?-labels (a global ?done collides with game_vm op_shl ?done).
;-----------------------------------------------------------------------------
.proc snd_irq
        pha
        lda IRQEN                    ; bit0 = 0 -> Timer 1 pending (ours)
        and #$01
        beq ?ours
        pla
        jmp (snd_old_iir)            ; not ours -> chain (serial IRQs during loads)
?ours   lda POKMSK                   ; acknowledge + re-arm Timer 1 (POKMSK-based:
        and #$FE                     ;   SIO owns POKMSK serial bits during loads)
        sta IRQEN
        lda POKMSK
        sta IRQEN
        lda snd_active
        beq ?off                     ; 0 = stray after silence -> mute + disable
        cmp #2
        beq ?lo
        ; --- state 1 / phase 0 : read the VRAM byte, output the HI nibble ---
        lda zsnd_bank
        sta VBXE_MEMAC_B
snd_rd  lda $4000                    ; operand = byte ptr (snd_play / phase 1 patch it)
        sta zsnd_cur                 ; lo nibble parked for phase 1
        lsr @                        ; hi nibble -> AUDC4 ASAP (shift in A: the
        lsr @                        ;   sample bank stays mapped, nothing reads
        lsr @                        ;   VRAM here; restore AFTER the output)
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
        cmp #$80                     ; crossed the 16 KB window -> next snd_blist bank
        bne ?pgok
        lda #$40
        sta snd_rd+2
        inc snd_blidx
        stx snd_xsave                ; X used only here (paid ~1/16384 bytes)
        ldx snd_blidx
        lda snd_blist,x
        sta zsnd_bank
        ldx snd_xsave
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
