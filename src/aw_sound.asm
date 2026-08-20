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
;
;   COVOX (8-bit linear DAC at $D280 -- PokeyMAX or a compatible card)
;     -- AUTO-DETECTED at snd_init, see snd_detect.
;     Found     : the SAME nibble streams are re-routed to the covox instead of
;                 to POKEY. The win is NOT resolution (the data stays 4-bit, it
;                 has to -- VRAM is full, see below) but LINEARITY: POKEY's
;                 volume-only DAC is visibly bent (hence the voltab curves) and
;                 summing its channels compresses, an R-2R ladder does neither.
;                 Each voice parks its current level in a ZP byte and the shared
;                 IRQ tail sums them into ONE write -- covox is a single DAC, it
;                 has no per-channel mixer like POKEY's four AUDCs.
;     Not found : nothing changes; the POKEY code is what is assembled in place.
;     The switch is one-way SMC applied once at init (snd_go_covox), so the hot
;     IRQ pays NOTHING for the mode it is not in -- no flag test per tick.
;     Why not 8-bit SAMPLES: one byte would then be one sample instead of two
;     nibbles, i.e. 2x the bytes -- 234 KB of music + 127 KB of SFX against 32
;     VRAM banks that are already fully allocated (aw_data.asm). Not possible
;     without cutting content, so variant A (same data, better DAC) it is.
;=============================================================================
; POKEY registers
AUDF1   = $D200
AUDC1   = $D201
AUDC2   = $D203
AUDC3   = $D205
AUDC4   = $D207
AUDCTL  = $D208
STIMER  = $D209
RANDOM  = $D20A                      ; r: 17-bit poly; frozen to $FF while SKCTL b0-1 = 0
IRQEN   = $D20E                      ; write = IRQ enable; read = IRQ status
SKCTL   = $D20F
POKMSK  = $0010
VIMIRQ  = $0216

; COVOX write ports = the PokeyMAX COVOX block ($D280-$D283, four 8-bit
; VOLONLYCHn latches). Channels 1 and 4 ($D280/$D283) feed LEFT, channels 2 and
; 3 ($D281/$D282) feed RIGHT, so ONE write to $D280 plus one to $D281 gives a
; balanced mono image. Altirra's 4-channel Covox device at base $D280 decodes
; addr&3 the same way (0,3 = L / 1,2 = R), so the same pair works there.
;   NOT $D284/$D286, which is what this player used to write: only a STOCK
;   POKEY mirrors A0-A3 across the whole $D2 page. A PokeyMAX decodes
;   $D280-$D29F itself and $D284-$D29F are its SAMPLE registers (RAMADDRL/H,
;   RAMDATA, RAMDATAINC, CHANSEL, SAMPER, SAMVOL, SAMDMA ...) -- sample bytes
;   sent there just walk the internal block-RAM pointer and make NO sound at
;   all. That is exactly what happened on real hardware: dead silence, because
;   the player had already stopped writing POKEY. See pokeymax.txt.
;   The flip side: with no covox in the machine $D280 IS a POKEY mirror, and it
;   mirrors AUDF1 -- the Timer 1 divider this player's IRQ runs on. So the mode
;   may only be entered on POSITIVE evidence; see snd_detect.
COVOXL  = $D280                      ; VOLONLYCH1 -> left
COVOXR  = $D281                      ; VOLONLYCH2 -> right
CVPROBE = $D28F                      ; SKCTL on a stock POKEY / SAMVOL on a
                                     ;   PokeyMAX -- see snd_detect

; PokeyMAX identification + configuration (pokeymax.txt). Only ever touched
; after the ID register has answered: a stock POKEY has no register at $D20C
; and reads of unused registers return $FF, never 1.
PMCFG   = $D20C                      ; w: $3F banks the config registers into
                                     ;   $D210-$D21F, any other value unbanks
                                     ;   them.  r: ID, 1 = PokeyMAX
PMCAP   = $D211                      ; r: CAPABILITY -- bit4 = the core has COVOX
PMREST  = $D217                      ; rw: RESTRICT  -- bit4 = the COVOX/SAMPLE
                                     ;   area is mapped at $D280. The U1MB
                                     ;   plugin can switch it off, and then
                                     ;   $D280-$D29F is a POKEY shadow again
PMSDMA  = $D290                      ; w: SAMDMA -- per-channel sample DMA. The
                                     ;   DMA engine writes the very registers
                                     ;   this player writes, so it must be off

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

; COVOX mix accumulators ($80/$81 = the old pl_lo/pl_mid, free since pl_byte
; moved to pl_wlo/pl_whi+pl_bnk -- see aw_equates.inc). UNUSED in POKEY mode.
;   The music side parks the RAW nibble (0..15) and the tail scales it <<3, so
;   the hot phase code is just `sta a:mus_out` and stays inside the 5-byte slot
;   that holds POKEY's `ora #$10 / sta AUDC2`. The SFX side is already scaled by
;   the (linear, voltab8) curve, so it parks a finished 0..127 level.
mus_out    = $80                     ; music nibble 0..15   (silence = 8  -> <<3 = 64)
sfx_out    = $81                     ; SFX level   0..127   (silence = 64)

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
cv_next      dta $80                 ; COVOX: the mixed byte the NEXT tick outputs.
                                     ;   The DAC is written from the IRQ HEAD, at a
                                     ;   fixed offset from entry, never from the tail:
                                     ;   the tail sits behind ~40 cycles of variable
                                     ;   body (phase 0 vs phase 1 vs hold tick), so
                                     ;   output instants would alternate ~+-11 us
                                     ;   about a 250 us period. That is sampling
                                     ;   jitter, and at 1 kHz its error (~-23 dB) is
                                     ;   on par with the 4-bit quantization it would
                                     ;   be carrying -- it would eat the very
                                     ;   cleanliness this mode exists for. One tick
                                     ;   of latency buys a steady sample clock.
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
        sta AUDC3                    ; ch3 unused; ch4 is the SFX voice and must
        sta AUDC4                    ;   start silent
        lda #15
        sta AUDF1                    ; ~3995 Hz
        lda #0
        sta snd_active
        sta zmus_st
.ifdef COVOX_FORCE
        jsr snd_go_covox             ; TEST BUILD (build.ps1 -ForceCovox): skip the
                                     ;   probe and go 8-bit unconditionally. POKEY is
                                     ;   then never written, so pulling the Covox
                                     ;   device in Altirra must give SILENCE -- which
                                     ;   is what proves the covox path is the one
                                     ;   actually making the sound.
.else
        jsr snd_detect               ; -> 8-bit covox output if one answers
.endif
        jsr snd_mute                 ; park both voices (covox: DAC to mid rail)
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
; snd_detect : is a COVOX listening at $D280? Switches the player to 8-bit
;   output if so; leaves the assembled POKEY code alone if not.
;
;   THREE probes, because a false positive is expensive: with no covox in the
;   machine $D280 is a POKEY mirror of AUDF1, the Timer 1 divider this IRQ runs
;   on, so every sample would rewrite the tick rate. Nothing is switched over
;   until $D280 has POSITIVELY answered as a latch, or until a card has proven
;   it eats $D2xx writes POKEY would otherwise take.
;
;   1) READ-BACK (cv_probe) -- the real hardware, a PokeyMAX with its COVOX
;      block mapped. Its VOLONLYCHn registers are R/W and hand back exactly
;      what was written. A stock POKEY answers a read of $D280 with POT0
;      instead, and POT0 cannot match TWO patterns: it is either constant, or a
;      pot counter that only ever counts UP -- so $80 followed by $7F is
;      impossible for it. Both patterns sit at mid rail, so a covox that IS
;      there cannot click on the probe either.
;   2) POKEYMAX CONFIG (pm_try) -- the core HAS a covox, but its owner (or the
;      U1MB plugin) switched the COVOX/SAMPLE area off in RESTRICT, so $D280 is
;      still a POKEY shadow and probe 1 said no. Identify the card by its ID
;      register, switch the area on, re-run probe 1. cv_unrestrict puts
;      RESTRICT back from snd_stop, so the machine the game is handed is the
;      machine the intro started with.
;   3) SWALLOW -- a write-only card that inhibits POKEY's chip select, which is
;      how Altirra models a $D2xx covox (covox.cpp: passWrites = false for base
;      $D280) and the only way such a card can be seen at all. $D28F is SKCTL
;      on a stock POKEY, and with SKCTL bits 0-1 clear POKEY holds its polys in
;      reset -- RANDOM then reads a constant $FF, whereas a running poly gives
;      a different byte on every read. Write $80 there and AND 8 RANDOM reads:
;        $FF        -> POKEY took the write, polys frozen  -> NO covox
;        anything   -> the write vanished into a latch      -> COVOX
;      (8 running-poly reads ANDing to $FF needs all 8 bits set in all 8
;      samples, ~2^-64.) SKCTL is restored either way -- the keyboard scan the
;      ESC skip in aw_replayer.asm reads depends on it.
;
;   AUDF1 is rewritten at the end of both exits: on a machine without a covox
;   probe 1's two writes landed on it.
;   Invisible, and NOT because nobody tried: a covox anywhere other than $D2xx
;   cannot be found at all. It is a write-only latch, so there is no read-back,
;   and at $D100/$D500/$D600/$D700 it sits where nothing else answers, so there
;   is no swallowed write to notice either -- a probe has literally nothing to
;   observe. Altirra offers $D600-$D63F for exactly the setup that leaves VBXE
;   ($D640-$D65F) alone, so such a card is a real thing on a VBXE machine; it
;   just cannot be autodetected by anyone, only chosen by hand.
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
        and RANDOM
        and RANDOM
        and RANDOM
        and RANDOM
        ldx #$03
        stx SKCTL                    ; restore POKEY before anything reads keys
        cmp #$FF
        beq ?none                    ; frozen -> POKEY answered -> stay 4-bit
?yes    lda #15
        sta AUDF1                    ; ~3995 Hz again (probe 1 hits AUDF1 when
        jmp snd_go_covox             ;   there is no covox to swallow it)
?none   lda #15
        sta AUDF1
        rts
.endp

;-----------------------------------------------------------------------------
; cv_probe : C = 1 if $D280 is a read/write latch, i.e. a covox and not POKEY.
;   On success it also kills sample DMA: on a PokeyMAX the DMA engine writes
;   the very VOLONLYCHn registers this player writes, so a channel some earlier
;   program left running would fight every sample. $D290 may only be written
;   once $D280 has proven it is not POKEY -- $D290 is AUDF1 on a stock chip.
;   Clobbers A.
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
; pm_try : a PokeyMAX whose COVOX/SAMPLE area is switched off answers probe 1
;   as POKEY, because $D280-$D29F is then a shadow of POKEY 1. Identify the
;   card, switch the area on in RESTRICT, re-probe. C = 1 if $D280 answers now;
;   anything unexpected puts RESTRICT straight back and returns C = 0.
;   Clobbers A.
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
        sta cv_rest                  ; remember it, cv_unrestrict puts it back
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
; cv_unrestrict : put RESTRICT back the way pm_try found it. Called from
;   snd_stop, and by pm_try itself when switching the area on changed nothing.
;   Does nothing unless pm_try actually wrote it. Clobbers A.
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
        sta AUDC4                    ; $D217 is AUDC4 on a stock POKEY: if the
                                     ;   ID register ever lied, this takes the
                                     ;   volume that write left back off again
?out    rts
.endp

cv_rest dta $FF                      ; RESTRICT as pm_try found it ($FF = not us)

;-----------------------------------------------------------------------------
; snd_go_covox : ONE-WAY switch to 8-bit covox output. Walks cvpatch and copies
;   each covox code image over the POKEY code assembled in place. Runs once,
;   from snd_detect, before the IRQ is hooked -- there is no way back and no
;   runtime mode flag, which is exactly why the IRQ costs nothing for it.
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
        sta COVOXL+2                 ;   NOT drive. On a 4-channel card they sum
        sta COVOXL+3                 ;   into the same two outputs the driven
                                     ;   pair does ($D282 -> R, $D283 -> L, both
                                     ;   here and on a PokeyMAX), and probe 3
                                     ;   just left $80 in ch4: its address HAS
                                     ;   to be $D28F to hit SKCTL on a plain
                                     ;   POKEY, and $D28F & 3 = 3. Left alone
                                     ;   that is a permanent DC offset on the
                                     ;   left channel -- half the headroom gone
                                     ;   and the stereo image pulled off centre.
                                     ;   Safe to write: getting here means these
                                     ;   addresses have been PROVEN not to be
                                     ;   POKEY (where $D283 would be AUDC2, the
                                     ;   music voice).
        rts
?len    dta 0
.endp

;-----------------------------------------------------------------------------
; cvpatch : {dest, length, source image}. Ordered as the code below appears.
;-----------------------------------------------------------------------------
cvpatch dta a(snd_irq.mo0),    5, a(cv_mo)     ; music phase 0 output slot
        dta a(snd_irq.mo1),    5, a(cv_mo)     ; music phase 1 output slot
        dta a(snd_irq.ms0+1),  1, a(cv_m8)     ; music end : silence value
        dta a(snd_irq.ms1+1),  2, a(cv_mus)    ; music end : silence target
        dta a(snd_irq.so0+1),  2, a(cv_sfx)    ; SFX phase 0 store target
        dta a(snd_irq.so1+1),  2, a(cv_sfx)    ; SFX phase 1 store target
        dta a(snd_irq.ss0+1),  1, a(cv_s64)    ; SFX end : silence value
        dta a(snd_irq.ss1+1),  2, a(cv_sfx)    ; SFX end : silence target
        dta a(snd_irq.cvtail),CVTAIL_LEN, a(cv_tail)   ; IRQ tail -> mix tail
        dta a(snd_mute.mm0+1), 1, a(cv_m8)
        dta a(snd_mute.mm1+1), 2, a(cv_mus)
        dta a(snd_mute.mm2+1), 1, a(cv_s64)
        dta a(snd_mute.mm3+1), 2, a(cv_sfx)
        dta a(snd_mute.mm4),   1, a(cv_lda)    ; rts -> lda #$80 (park the DAC)
        dta a(vb1+1),          1, a(cv_vtl)    ; snd_play : voltab -> voltab8
        dta a(vb2+1),          1, a(cv_vth)
        dta a(vi1+1),          1, a(cv_irql)   ; snd_init : VIMIRQ -> cv_irq
        dta a(vi2+1),          1, a(cv_irqh)
        dta a(0)

;-----------------------------------------------------------------------------
; COVOX code images. NEVER executed here -- snd_go_covox copies them over the
; POKEY code. Written as real instructions so the assembler computes the bytes
; and the two variants stay readable side by side.
;-----------------------------------------------------------------------------
cv_mo   sta a:mus_out                ; park the RAW nibble; the mix tail scales
        nop                          ;   it. Padded to the 5 bytes POKEY spends
        nop                          ;   on `ora #$10 / sta AUDC2`.
cv_sfx  dta a(sfx_out)               ; `sta AUDC4` operand -> sfx_out
cv_mus  dta a(mus_out)               ; `sta AUDC2` operand -> mus_out
cv_m8   dta 8                        ; music silence : nibble 8 -> <<3 = 64
cv_s64  dta 64                       ; SFX silence   : level 64
cv_lda  dta $A9                      ; snd_mute : rts -> lda #imm
cv_vtl  dta <voltab8                 ; the LINEAR curves replace the POKEY ones
cv_vth  dta >voltab8
cv_irql dta <cv_irq                  ; the covox IRQ prologue replaces snd_irq's
cv_irqh dta >cv_irq

cv_tail lda mus_out                  ; ---- the covox mix tail ----
        asl @                        ; nibble<<3 -> 0..120 (mid 64). The value
        asl @                        ;   is <= $0F so every bit shifted out is
        asl @                        ;   0 -> C stays clear -> no clc needed.
        adc sfx_out                  ; + 0..127 -> 0..247; two silent voices
        sta cv_next                  ;   sum to exactly $80 = the covox centre.
                                     ;   Handed to the NEXT tick's head, not
                                     ;   written here -- see cv_next / cv_irq.
        lda memb_cur                 ; (then the normal POKEY-build tail)
        sta VBXE_MEMAC_B
        pla
        rti
cv_tail_end
CVTAIL_LEN = cv_tail_end-cv_tail

;-----------------------------------------------------------------------------
; cv_irq : the COVOX build's IRQ entry (snd_init's VIMIRQ immediates are patched
;   to point here). It is only the prologue that differs: acknowledge, push the
;   byte the previous tick mixed out to the DAC at a CONSTANT offset from entry
;   (~36 cyc -- the whole point, see cv_next), then fall into the shared body.
;   Everything after this -- both voices, the bank walk, the tail -- is the same
;   code the POKEY build runs.
;-----------------------------------------------------------------------------
.proc cv_irq
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
        lda cv_next
        sta COVOXL
        sta COVOXR
        jmp snd_irq.body
.endp

;-----------------------------------------------------------------------------
; snd_mute : mode-aware silence for BOTH voices. Clobbers A only (X/Y stay put
;   -- the IRQ's ?off path calls it). In covox mode the SMC'd rts turns into
;   `lda #$80` so it falls through to parking the DAC at mid rail: leaving it
;   wherever the last sample stopped would be a DC step, i.e. a click.
;-----------------------------------------------------------------------------
.proc snd_mute
mm0     lda #$00                     ; SMC imm  : $00 POKEY / $08 covox nibble
mm1     sta AUDC2                    ; SMC oper : AUDC2 / mus_out
mm2     lda #$00                     ; SMC imm  : $00 POKEY / $40 covox level
mm3     sta AUDC4                    ; SMC oper : AUDC4 / sfx_out
mm4     rts                          ; SMC op   : $60 rts / $A9 -> lda #$80
        dta $80
        sta COVOXL
        sta COVOXR
        sta cv_next                  ; and the pending byte, so a re-armed timer
        rts                          ;   cannot push a stale sample on tick 1
.endp

;-----------------------------------------------------------------------------
; snd_stop : silence both voices and unhook the Timer 1 IRQ (VIMIRQ back to the
;   OS handler). MUST run before the intro hands control to the boot loader
;   (ESC skip / intro_done chain): SIO relies on the serial IRQs running through
;   VIMIRQ, and the loader overwrites the RAM snd_irq lives in.
;-----------------------------------------------------------------------------
snd_stop
        sei
        jsr snd_mute
        lda POKMSK
        and #$FE                     ; Timer 1 off
        sta POKMSK
        sta IRQEN
        lda snd_old_iir
        sta VIMIRQ
        lda snd_old_iir+1
        sta VIMIRQ+1
        jsr cv_unrestrict            ; leave the PokeyMAX config as we found it
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
vb1     adc #<voltab                   ; SMC imm : voltab / voltab8 (covox mode
        sta ?vs+1                      ;   swaps the curve set, see cvpatch)
vb2     lda #>voltab
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
body    lda zmus_st                  ; cv_irq jumps in here (shared from now on)
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
mo0     ora #$10                     ; 5-byte SMC slot (see cv_mo): POKEY writes
        sta AUDC2                    ;   the volume-only nibble to AUDC2, covox
                                     ;   parks it for the mix tail
        lda #2
        sta zmus_st                  ; state 2 = phase 1 next
        bne ?sfx                     ; always
?m_lo   ; --- state 2 / phase 1 : output the LO nibble, count, advance ---
        lda mus_cur
        and #$0F
mo1     ora #$10                     ; same 5-byte slot as mo0
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
ms0     lda #$00                     ; SMC imm  : $00 POKEY / $08 covox nibble
ms1     sta AUDC2                    ; SMC oper : AUDC2 / mus_out
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
so0     sta AUDC4                    ; SMC oper : AUDC4 / sfx_out. POKEY curve
                                     ;   entries are pre-ORed with $10; the
                                     ;   covox ones are linear 0..127 (voltab8)
        lda #2
        sta snd_active               ; state 2 = phase 1 next
        bne ?tail                    ; always
?s_lo   ; --- state 2 / phase 1 : output the LO nibble, count, advance ---
        lda zsnd_cur
        and #$0F
        sta ?vl+1                    ; volume curve, as in phase 0
?vl     lda snd_vt
so1     sta AUDC4                    ; SMC oper : AUDC4 / sfx_out (as so0)
        lda #1
        sta snd_active               ; state 1 = phase 0 next
        inc snd_rem                  ; rem++ toward $0000 (stored negated)
        bne ?adv                     ; common case: 9 cyc total
        inc snd_rem+1
        bne ?adv
        lda #0                       ; sample done -> SFX off; keep the timer if
        sta snd_active               ;   the music is still streaming
ss0     lda #$00                     ; SMC imm  : $00 POKEY / $40 covox level
ss1     sta AUDC4                    ; SMC oper : AUDC4 / sfx_out
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
?tail
cvtail  lda memb_cur                 ; restore the poly/playlist bank (a no-op
        sta VBXE_MEMAC_B             ;   write when nothing was read this tick)
        pla
        rti
        ; The bytes below are DEAD in the POKEY build (the rti above ends the
        ; tick) -- they are the room snd_go_covox overwrites with cv_tail, the
        ; mix-and-output tail. Sizing is asserted by the two guards below, so a
        ; future edit to either version cannot silently run off the end.
        :[CVTAIL_LEN-7] dta 0
cvtail_end
        ert cvtail_end-cvtail>CVTAIL_LEN
        ert cvtail_end-cvtail<CVTAIL_LEN
?off    jsr snd_mute                 ; both voices idle -> mute + disable Timer 1
        lda POKMSK
        and #$FE
        sta POKMSK
        sta IRQEN
        pla
        rti
.endp

;-----------------------------------------------------------------------------
        icl 'src/aw_sfx_tables.inc'           ; SFX_COUNT, sfx_bank/winlo/winhi/lenlo/lenhi
        icl 'src/aw_voltab8.inc'              ; voltab8 : the LINEAR curves (covox mode)
