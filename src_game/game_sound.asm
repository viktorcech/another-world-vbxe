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
;   COVOX (8-bit linear DAC) -- AUTO-DETECTED at snd_init, see snd_detect. Same
;   deal as the intro (src/aw_sound.asm): the 4-bit nibbles are unchanged, they
;   just go to a linear R-2R ladder instead of POKEY's bent volume-only DAC.
;   One voice here, so there is nothing to mix -- the nibble is scaled <<4 into
;   a full 0..240 byte (the intro has to share the range between two voices).
;   The switch is one-way SMC applied once at init, so this IRQ -- which fires
;   at up to 21 kHz -- pays NOTHING for the mode it is not in.
;   The init-only half (probe, patcher, images) plus the covox IRQ prologue live
;   in the free $0900-$0FFF low RAM, because $2000-$3FFF has ~77 bytes left.
;=============================================================================
AUDF1   = $D200
AUDC1   = $D201
AUDC3   = $D205
AUDC4   = $D207
AUDCTL  = $D208
STIMER  = $D209
RANDOM  = $D20A                      ; r: 17-bit poly; frozen to $FF while SKCTL b0-1 = 0
IRQEN   = $D20E
SKCTL   = $D20F
POKMSK  = $0010
VIMIRQ  = $0216

; COVOX write ports -- see src/aw_sound.asm for the full reasoning. Short form:
; only a STOCK POKEY mirrors A0-A3 across the whole $D2 page. A PokeyMAX decodes
; $D280-$D29F itself: $D280-$D283 are its four 8-bit COVOX latches (ch1/ch4 =
; LEFT, ch2/ch3 = RIGHT) and $D284-$D29F are the SAMPLE registers -- so the
; $D284/$D286 this player used to write were RAMADDRL and RAMDATA, which move a
; block-RAM pointer and make no sound whatsoever. $D280 + $D281 is one channel
; per side, and matches Altirra's 4-channel Covox device (addr&3: 0,3 = L).
;   Where there is NO covox $D280 mirrors AUDF1 -- which this player rewrites
;   for every sound (snd_audf) to set the sample rate -- so the mode may only be
;   entered on positive evidence. See snd_detect.
COVOXL  = $D280                      ; VOLONLYCH1 -> left
COVOXR  = $D281                      ; VOLONLYCH2 -> right
CVPROBE = $D28F                      ; SKCTL on a stock POKEY / SAMVOL on a
                                     ;   PokeyMAX -- see snd_detect

; PokeyMAX identification + configuration (pokeymax.txt); only touched after
; the ID register answers 1, which a stock POKEY cannot do -- reads of its
; unused registers give $FF.
PMCFG   = $D20C                      ; w: $3F banks config -> $D210-$D21F  r: ID
PMCAP   = $D211                      ; r: CAPABILITY -- bit4 = core has COVOX
PMREST  = $D217                      ; rw: RESTRICT  -- bit4 = COVOX/SAMPLE area
                                     ;   mapped at $D280
PMSDMA  = $D290                      ; w: SAMDMA -- sample DMA writes the same
                                     ;   registers this player does; keep it off

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
cv_next       dta $80                ; COVOX: the byte the NEXT tick pushes to the DAC.
                                     ;   Written from the IRQ HEAD at a fixed offset
                                     ;   (cv_irq), never inline: phase 0 reaches its
                                     ;   output ~4 cyc later than phase 1, and at
                                     ;   21 kHz (83 cyc/tick) that alternating skew is
                                     ;   real sampling jitter. One tick of latency
                                     ;   buys a steady sample clock.

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
        sta AUDC3                    ; ch3 unused; ch4 is the sample voice and
        sta AUDC4                    ;   must start silent
        lda #15
        sta AUDF1
        lda #0
        sta snd_active
.ifdef COVOX_FORCE
        jsr snd_go_covox             ; TEST BUILD (build.ps1 -ForceCovox): see the
.else                                ;   note in src/aw_sound.asm
        jsr snd_detect               ; -> 8-bit covox output if one answers
.endif
        sei
        lda VIMIRQ
        sta snd_old_iir
        lda VIMIRQ+1
        sta snd_old_iir+1
vi1     lda #<snd_irq                ; SMC imm : snd_irq / cv_irq (covox prologue)
        sta VIMIRQ
vi2     lda #>snd_irq
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
body    lda snd_active               ; cv_irq jumps in here (shared from now on)
        beq ?off                     ; 0 = stray after silence -> mute + disable
        cmp #2
        beq ?lo
        ; --- state 1 / phase 0 : read the VRAM byte, output the HI nibble ---
        lda zsnd_bank
        sta VBXE_MEMAC_B
snd_rd  lda $4000                    ; operand = byte ptr (snd_play / phase 1 patch it)
        sta zsnd_cur                 ; lo nibble parked for phase 1
cp0     lsr @                        ; hi nibble -> AUDC4 ASAP (shift in A: the
        lsr @                        ;   sample bank stays mapped, nothing reads
        lsr @                        ;   VRAM here; restore AFTER the output)
        lsr @
        ora #$10                     ; 9-byte SMC slot (cv_p0): covox does the same
        sta AUDC4                    ;   job as `and #$F0 / sta cv_next` -- A still
                                     ;   holds the RAW byte here, so its top nibble
                                     ;   IS the 0..240 sample, no shifting needed
        lda memb_cur                 ; restore the poly/playlist bank
        sta VBXE_MEMAC_B
        lda #2
        sta snd_active               ; state 2 = phase 1 next
        pla
        rti
?lo     ; --- state 2 / phase 1 : output the LO nibble, count, advance ---
        lda zsnd_cur
cp1     and #$0F                     ; 7-byte SMC slot (cv_p1): covox uses `asl @ x4`
        ora #$10                     ;   instead, which shifts the LO nibble up to
        sta AUDC4                    ;   0..240 and drops the high one in one go
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
        ; ?stop and ?off used to be two copies of the same teardown; merging them
        ; paid for the wider silence slot below (net -7 bytes, and $2000-$3FFF has
        ; ~77 to spare).
?stop   lda #0
        sta snd_active               ; fall through
?off
cvm     lda #$00                     ; 11-byte SMC slot (cv_off): POKEY just silences
        sta AUDC4                    ;   AUDC4, covox parks BOTH ports and the pending
        nop                          ;   byte at $80 = mid rail, so a re-armed timer
        nop                          ;   cannot push a stale sample on tick 1 and the
        nop                          ;   DAC does not sit on a DC step
        nop
        nop
        nop
        lda POKMSK
        and #$FE
        sta POKMSK
        sta IRQEN
        pla
        rti
.endp

;=============================================================================
; COVOX support. Relocated to the free $0900-$0FFF low RAM (awgame.asm:220 --
; the boot loader's area is dead once it JMPs to the game and every other
; segment starts >= $1000), because the $2000-$3FFF chain has ~77 bytes left.
; Same idiom aw_raster.asm uses for adv_edges1: `org` here, `org` back after.
;=============================================================================
covox_resume equ *
        org $0BC0

;-----------------------------------------------------------------------------
; snd_detect : is a COVOX listening at $D280? Switches this player to 8-bit
;   output if so, otherwise returns having changed nothing. Three probes -- the
;   long version of the reasoning is in src/aw_sound.asm.
;
;   1) READ-BACK (cv_probe): a PokeyMAX's COVOX registers are R/W and give back
;      what was written; a stock POKEY answers a read of $D280 with POT0, which
;      cannot match $80 AND THEN $7F (it is constant, or a counter that only
;      counts up). Both patterns are mid rail, so the probe cannot click.
;   2) POKEYMAX CONFIG (pm_try): the core has a covox but the COVOX/SAMPLE area
;      is switched off in RESTRICT, so $D280 is still a POKEY shadow. Identify
;      the card by its ID register, switch the area on, re-run probe 1.
;   3) SWALLOW: $D28F is SKCTL on a stock POKEY, and with bits 0-1 clear the
;      polys freeze so RANDOM reads a constant $FF. Write $80 there and AND
;      four RANDOM reads -- $FF means POKEY took it (no card), anything else
;      means a latch ate it (covox). ~2^-32 for a false "covox". SKCTL is put
;      back either way; the keyboard scan depends on it. This is the probe
;      Altirra's Covox device answers (covox.cpp: passWrites = false).
;
;   AUDF1 is restored on both exits: without a covox, probe 1 wrote to it.
;-----------------------------------------------------------------------------
.proc snd_detect
        jsr cv_probe                 ; 1: does $D280 read back what we write?
        bcs ?yes
        jsr pm_try                   ; 2: a PokeyMAX with its COVOX area off?
        bcs ?yes
        lda #$03                     ; 3: does a write to $D28F reach POKEY?
        sta SKCTL                    ; make sure the polys ARE running first
        lda #$80
        sta CVPROBE                  ; covox: mid-rail latch / POKEY: polys reset
        bit RANDOM                   ; >10 cyc must pass before RANDOM reads $FF
        bit RANDOM
        bit RANDOM
        bit RANDOM
        lda #$FF
        and RANDOM
        and RANDOM
        and RANDOM
        and RANDOM
        ldx #$03
        stx SKCTL                    ; restore POKEY before anything reads keys
        cmp #$FF
        beq ?none                    ; frozen -> POKEY answered -> stay 4-bit
?yes    lda #15
        sta AUDF1                    ; op_sound overwrites this per sound, but
        jmp snd_go_covox             ;   probe 1 must not leave it garbage
?none   lda #15
        sta AUDF1
        rts
.endp

;-----------------------------------------------------------------------------
; cv_probe : C = 1 if $D280 is a read/write latch, i.e. a covox and not POKEY.
;   Also kills sample DMA on success -- the PokeyMAX DMA engine writes the same
;   VOLONLYCHn registers this player writes. $D290 may only be written once
;   $D280 has proven it is not POKEY ($D290 is AUDF1 on a stock chip, and this
;   player lives on AUDF1). Clobbers A.
;-----------------------------------------------------------------------------
.proc cv_probe
        lda #$80
        sta COVOXL
        cmp COVOXL
        bne ?no
        lda #$7F
        sta COVOXL
        cmp COVOXL
        bne ?no
        lda #0
        sta PMSDMA                   ; sample DMA off on all four channels
        sec
        rts
?no     clc
        rts
.endp

;-----------------------------------------------------------------------------
; pm_try : switch the COVOX/SAMPLE area on in a PokeyMAX that has it disabled,
;   then re-probe. C = 1 if $D280 answers now. Anything unexpected puts
;   RESTRICT straight back and returns C = 0. Clobbers A.
;   Unlike the intro this player has no teardown path (the game runs to a
;   reset), so a RESTRICT it switched on stays on -- it only maps hardware that
;   was already in the machine, and the intro restored its own before chaining.
;-----------------------------------------------------------------------------
.proc pm_try
        lda PMCFG                    ; ID: 1 = PokeyMAX, $FF = plain POKEY
        cmp #1
        bne ?no
        lda #$3F
        sta PMCFG                    ; config registers -> $D210-$D21F
        lda PMCAP
        and #$10                     ; does this core have a COVOX at all?
        beq ?unmap
        lda PMREST
        sta cv_rest
        ora #$10                     ; COVOX/SAMPLE area on
        sta PMREST
        lda #0
        sta PMCFG                    ; unbank before touching $D280 again
        jsr cv_probe
        bcs ?out
        jsr cv_unrestrict            ; still nothing there -> undo, stay 4-bit
        clc
        rts
?unmap  lda #0
        sta PMCFG
?no     clc
?out    rts
.endp

;-----------------------------------------------------------------------------
; cv_unrestrict : put RESTRICT back the way pm_try found it. Clobbers A.
;-----------------------------------------------------------------------------
.proc cv_unrestrict
        lda cv_rest
        bmi ?out                     ; $FF = not ours (RESTRICT is 5 bits)
        pha
        lda #$3F
        sta PMCFG
        pla
        sta PMREST
        lda #0
        sta PMCFG
        lda #$FF
        sta cv_rest
        lda #0
        sta AUDC4                    ; $D217 is AUDC4 on a stock POKEY: undo the
                                     ;   volume that write left, if the ID lied
?out    rts
.endp

cv_rest dta $FF                      ; RESTRICT as pm_try found it ($FF = not us)

;-----------------------------------------------------------------------------
; snd_go_covox : ONE-WAY switch to 8-bit covox output. Walks cvpatch and copies
;   each covox image over the POKEY code assembled in place, then parks the DAC.
;   Runs once, from snd_detect, before the IRQ is hooked -- no way back and no
;   runtime mode flag, which is why the IRQ costs nothing for it.
;-----------------------------------------------------------------------------
.proc snd_go_covox
        ldy #0
?ent    lda cvpatch,y                ; dest lo
        sta ?st+1
        iny
        lda cvpatch,y                ; dest hi ($00 terminates)
        beq ?done
        sta ?st+2
        iny
        lda cvpatch,y                ; byte count
        sta ?len
        iny
        lda cvpatch,y                ; source image
        sta ?ld+1
        iny
        lda cvpatch,y
        sta ?ld+2
        iny
        ldx #0
?ld     lda $FFFF,x                  ; operand patched above
?st     sta $FFFF,x                  ; operand patched above
        inx
        cpx ?len
        bne ?ld
        beq ?ent                     ; always
?done   lda #0                       ; silence the two channels this player does
        sta COVOXL+2                 ;   NOT drive: on a 4-channel card they sum
        sta COVOXL+3                 ;   into the same outputs ($D282 -> R,
                                     ;   $D283 -> L, on a PokeyMAX too), and
                                     ;   probe 3 left $80 in ch4 -- its address
                                     ;   has to be $D28F to hit SKCTL on a plain
                                     ;   POKEY, and $D28F & 3 = 3. Left there it
                                     ;   is a permanent DC offset on the left
                                     ;   channel. Safe: reaching here proves
                                     ;   these are not POKEY registers.
        lda #$80                     ; park both ports + the pending byte at mid rail
        sta COVOXL
        sta COVOXR
        sta cv_next
        rts
?len    dta 0
.endp

;-----------------------------------------------------------------------------
; cvpatch : {dest, length, source image}.
;-----------------------------------------------------------------------------
cvpatch dta a(snd_irq.cp0),  9, a(cv_p0)   ; phase 0 output slot
        dta a(snd_irq.cp1),  7, a(cv_p1)   ; phase 1 output slot
        dta a(snd_irq.cvm), 11, a(cv_off)  ; silence/teardown slot
        dta a(vi1+1),        1, a(cv_irql) ; snd_init : VIMIRQ -> cv_irq
        dta a(vi2+1),        1, a(cv_irqh)
        dta a(0)

;-----------------------------------------------------------------------------
; COVOX code images. NEVER executed here -- snd_go_covox copies them over the
; POKEY code. Written as real instructions so the assembler computes the bytes
; and both variants stay readable side by side.
;-----------------------------------------------------------------------------
cv_p0   and #$F0                     ; A still holds the RAW byte: its top nibble
        sta cv_next                  ;   IS the sample, 0..240. 4 bytes of padding
        nop                          ;   to fill POKEY's `lsr @ x4 / ora / sta` --
        nop                          ;   and the same 14 cycles, so phase 0 does
        nop                          ;   not even get slower.
        nop
cv_p0_end
cv_p1   asl @                        ; A = the raw byte again: 4 shifts push the LO
        asl @                        ;   nibble to 0..240 and drop the high one, so
        asl @                        ;   no `and #$0F` is needed
        asl @
        sta cv_next
cv_p1_end
cv_off  lda #$80                     ; mid rail on both ports AND on the pending byte
        sta COVOXL
        sta COVOXR
        sta cv_next
cv_off_end
cv_irql dta <cv_irq
cv_irqh dta >cv_irq

; slot-size guards: the covox image must fill its POKEY slot EXACTLY. One byte
; over and it eats the next instruction -- on the machines that have a covox,
; and only those. (tools/verify_covox.py re-checks this on the built binary.)
        ert cv_p0_end-cv_p0>9
        ert cv_p0_end-cv_p0<9
        ert cv_p1_end-cv_p1>7
        ert cv_p1_end-cv_p1<7
        ert cv_off_end-cv_off>11
        ert cv_off_end-cv_off<11

;-----------------------------------------------------------------------------
; cv_irq : the COVOX build's IRQ entry (snd_init's VIMIRQ immediates point here).
;   Only the prologue differs: acknowledge, then push the byte the previous tick
;   produced to the DAC at a CONSTANT offset from entry -- that is the whole
;   point, see cv_next -- and fall into the shared body.
;-----------------------------------------------------------------------------
.proc cv_irq
        pha
        lda IRQEN                    ; bit0 = 0 -> Timer 1 pending (ours)
        and #$01
        beq ?ours
        pla
        jmp (snd_old_iir)            ; not ours -> chain (serial IRQs during loads)
?ours   lda POKMSK                   ; acknowledge + re-arm Timer 1
        and #$FE
        sta IRQEN
        lda POKMSK
        sta IRQEN
        lda cv_next
        sta COVOXL
        sta COVOXR
        jmp snd_irq.body
.endp

        ert *>$0F80                  ; must not run into adv_edges1 (aw_raster.asm)
        org covox_resume
