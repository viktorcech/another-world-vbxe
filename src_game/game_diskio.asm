;=============================================================================
; game_diskio.asm  -  runtime part loader: SIO-read a part's RAW resources from
; the game ATR straight into VRAM, no DOS, no depacker.
;
;   The full game is too big for VRAM/RAM, so only ONE part is resident at a time.
;   load_part(index) overwrites the fixed VRAM banks with the new part:
;       video1 -> banks $14.. ($050000)   bytecode -> $18.. ($060000)
;       video2 -> banks $1C.. ($070000)   palette  -> RAM pal_data ($9000)
;   Sector tables come from tools/make_game_atr.py (out/game.atr + game_atr.inc).
;
;   Model + read_sectors mirror doom2d/source/diskio.asm. The AW VM runs with
;   IRQ off (sei); SIOV needs the serial IRQ, so load_part brackets the transfer
;   with cli/sei.
;=============================================================================

; SIO device control block (OS page 3)
SIOV     = $E459
DDEVIC   = $0300
DUNIT    = $0301
DCOMND   = $0302
DSTATS   = $0303
DBUFLO   = $0304
DBUFHI   = $0305
DTIMLO   = $0306
DBYTLO   = $0308
DBYTHI   = $0309
DAUX1    = $030A                    ; sector number low
DAUX2    = $030B                    ; sector number high

; POKEY / PIA registers for the in-RAM high-speed SIO path (private names so
; this file stays self-contained; cannot clash with game_sound's equates).
p_AUDF3  = $D204
p_AUDC3  = $D205
p_AUDF4  = $D206
p_AUDC4  = $D207
p_AUDCTL = $D208
p_SKRES  = $D20A
p_SERIN  = $D20D                    ; read
p_SEROUT = $D20D                    ; write
p_IRQST  = $D20E                    ; read = serial IRQ status
p_IRQEN  = $D20E                    ; write = IRQ enable
p_SKCTL  = $D20F
p_PBCTL  = $D303                    ; PIA CB2 = SIO COMMAND line

; disk loader scratch (persistent VM-state RAM, after the VM globals)
dk_sec   = $B3C8                    ; (2) current sector
dk_cnt   = $B3CA                    ; (2) remaining sector count
dk_bank  = $B3CC                    ; current VRAM bank
dk_n     = $B3CD                    ; sectors this chunk (<=128 = one 16K bank)
dk_idx   = $B3CE                    ; part index being loaded
hs_div   = $B3D6                    ; negotiated high-speed AUDF3 index ($01 = none -> stock SIOV)

        icl 'src_game/game_atr.inc' ; per-part {sector, count} tables + GAME_FIRST_PART

;=============================================================================
; read_sectors : SIO read X (1..255) 128-byte sectors.
;   in : DAUX1/2 = start sector, DBUFLO/HI = dest, X = count
;   out: C=0 ok / C=1 error ; advances DBUF + DAUX
;=============================================================================
RS_RETRY = 4
.proc read_sectors
        stx rs_cnt
?lp     lda #RS_RETRY
        sta rs_try
?try    jsr read_one                ; high speed if available, else stock SIOV
        bcc ?ok
        dec rs_try                  ; nobody up the chain looks at the carry, so a
        bne ?try                    ;   dropped sector used to become an unloaded
        sec                         ;   VRAM bank and a VM running garbage. Re-ask.
        rts
?ok     lda DBUFLO                  ; dest += 128
        clc
        adc #128
        sta DBUFLO
        bcc ?ni
        inc DBUFHI
?ni     inc DAUX1                   ; sector++
        bne ?na
        inc DAUX2
?na     dec rs_cnt
        bne ?lp
        clc
        rts
rs_cnt  dta 0
rs_try  dta 0
.endp

;-----------------------------------------------------------------------------
; read_one : read ONE 128-byte sector (DAUX -> DBUF). Uses the negotiated
;   high-speed divisor when usable; on any high-speed failure it disables HS
;   for the rest of this load (hs_div<-1) and retries the sector via SIOV.
;-----------------------------------------------------------------------------
.proc read_one
        lda hs_div
        cmp #$28                    ; >= $28 = 19200 -> no gain, use stock
        bcs ?std
        cmp #$02                    ; < 2 -> disabled/invalid, use stock
        bcc ?std
        jsr hs_read_sector
        bcc ?ok
        lda #$01                    ; HS read failed -> fall back for the rest
        sta hs_div
?std    jmp std_read_one
?ok     clc
        rts
.endp

;-----------------------------------------------------------------------------
; dcb_base : the DCB fields std_read_one and hs_poll set identically. Factored out
;   because the $B400 block ends at the $C000 OS-ROM ceiling and had no room left.
;-----------------------------------------------------------------------------
dcb_base
        lda #$31
        sta DDEVIC
        lda #$01
        sta DUNIT
        lda #$40                    ; receive data
        sta DSTATS
        lda #$0F
        sta DTIMLO
        rts

;-----------------------------------------------------------------------------
; std_read_one : stock OS SIOV read of one 128-byte sector (DAUX -> DBUF).
;-----------------------------------------------------------------------------
.proc std_read_one
        jsr dcb_base
        lda #$52                    ; read sector
        sta DCOMND
        lda #128
        sta DBYTLO
        lda #0
        sta DBYTHI
        jsr SIOV
        bmi ?err
        clc
        rts
?err    sec
        rts
.endp

;=============================================================================
; --- HIGH-SPEED SIO BLOCK, RELOCATED to the free $0DB0-$0F7F low RAM gap -----
; The $B400 block ends at the $C000 OS-ROM ceiling with ~9 bytes to spare, and the
; hardening below does not fit there.  $0DB0-$0F7F is free at RUN time: game_text
; has $0900-$0B31, game_sound's covox $0BC0-$0DAD, aw_raster's adv_edges1
; $0F80-$0FBA, the boot loader's area ends at $08FF and every other segment starts
; >= $1000.  Same `org` idiom as the covox block; check_xex.py verifies it.
;
; NOTE: this whole block is currently NOT CALLED -- load_part forces hs_div=1 so
; every sector goes through stock SIOV.  See the comment there.
;=============================================================================
diskio_resume equ *
        org $0DB0

;-----------------------------------------------------------------------------
; hs_poll : ask D1: for its high-speed SIO divisor (command $3F). Stores the
;   POKEY AUDF3 index in hs_div (2..$27 = usable), else $01 (none). Stock SIOV,
;   one command frame -- negligible vs the part stream. Drives without high
;   speed NAK $3F -> hs_div=$01 -> everything stays stock (unchanged behaviour).
;-----------------------------------------------------------------------------
.proc hs_poll
        jsr dcb_base
        lda #$3f                    ; poll high-speed index
        sta DCOMND
        lda #<hs_pbuf
        sta DBUFLO
        lda #>hs_pbuf
        sta DBUFHI
        lda #1
        sta DBYTLO
        lda #0
        sta DBYTHI
        jsr SIOV
        bmi ?none
        lda hs_pbuf
        cmp #$28                    ; >= 19200 -> no benefit
        bcs ?none
        cmp #$02                    ; sub-2 indices: skip (too tight + marker clash)
        bcc ?none
        sta hs_div
        rts
?none   lda #$01
        sta hs_div
        rts
hs_pbuf dta 0
.endp

;-----------------------------------------------------------------------------
; hs_read_sector : read ONE 128-byte sector at the negotiated high-speed
;   divisor (hs_div), POKEY-level, IRQ-polled. No OS SIO, no ROM patch.
;   in : DAUX1/2 = sector, DBUFLO/HI = dest
;   out: C=0 ok / C=1 fail (timeout or bad checksum)
;   Scratch: ZP $32/$33 = dest ptr, $34 = checksum, $35 = timeout hi.
;   Fails safe: a wrong/unsupported speed just times out -> C=1 (never hangs).
;-----------------------------------------------------------------------------
.proc hs_read_sector
        lda DBUFLO
        sta $32
        lda DBUFHI
        sta $33

        php
        sei

        ; POKEY: 16-bit serial clock at the high-speed divisor
        lda #$28
        sta p_AUDCTL
        lda #$a0
        sta p_AUDC3
        sta p_AUDC4
        lda hs_div
        sta p_AUDF3
        lda #$00
        sta p_AUDF4

        ; ---- send command frame: $31 $52 auxlo auxhi chk ----
        lda #$00
        sta p_SKCTL
        sta p_SKRES
        sta p_IRQEN
        lda #$23                    ; transmit mode
        sta p_SKCTL
        lda #$10
        sta p_IRQEN                 ; arm SEROR

        lda #$34                    ; assert COMMAND line
        sta p_PBCTL

        lda #$31                    ; device id = checksum seed
        sta $34
        jsr ?send_raw
        lda #$52                    ; read command
        jsr ?send_chk
        lda DAUX1
        jsr ?send_chk
        lda DAUX2
        jsr ?send_chk
        lda $34                     ; command-frame checksum
        jsr ?send_raw

        jsr ?wait_txr               ; last byte into the shift register...
        lda #$08
        sta p_IRQEN                 ; ...then wait for full transmit-complete
        ldy #$00                    ; BOUNDED, same reason as ?wait_txr
?txc    ldx #$00
?txc2   lda #$08
        bit p_IRQST
        beq ?txd
        dex
        bne ?txc2
        dey
        bne ?txc
?txd

        lda #$3c                    ; deassert COMMAND line
        sta p_PBCTL

        ; ---- receive ACK, COMPLETE, 128 data bytes, checksum ----
        lda #$00
        sta p_SKCTL
        sta p_SKRES
        sta p_IRQEN
        lda #$13                    ; async receive mode
        sta p_SKCTL
        lda #$20
        sta p_IRQEN                 ; arm SERIN

        jsr ?get                    ; ACK ($41)
        bcs ?fail
        cmp #$41
        bne ?fail
        jsr ?get                    ; COMPLETE ($43)
        bcs ?fail
        cmp #$43
        bne ?fail

        ldy #$00                    ; 128 data bytes + running checksum
        sty $34
?rx     jsr ?get
        bcs ?fail
        sta ($32),y
        clc
        adc $34
        adc #$00                    ; end-around carry (Atari SIO checksum)
        sta $34
        iny
        cpy #$80
        bne ?rx

        jsr ?get                    ; frame checksum
        bcs ?fail
        cmp $34
        bne ?fail

        jsr ?pokey_idle
        plp
        clc
        rts

?fail   jsr ?pokey_idle
        plp
        sec
        rts

; ?pokey_idle : put POKEY back the way the OS expects BEFORE plp lets IRQs in.
;   This used to leave SKCTL in async-receive mode AND IRQEN armed for SERIN
;   ($20, written by ?grdy). With IRQs back on and no OS SIO call in flight, one
;   stray byte on the line then raised a serial IRQ that ran VIMIRQ -> snd_irq ->
;   chain -> the OS serial handler, which stores through BUFRLO/BUFRHI ($30/$31)
;   and tests BFENLO/BFENHI -- and $32/$33 are this routine's destination pointer.
;   Result: silent corruption plus OS SIO flags left in a state that wedges the
;   NEXT SIOV -> "LOADING..." reads a bit and then hangs. Only reachable over
;   real SIO: SIDE3/AVG/SUB answer no $3F, so hs_div stays 1 and this whole
;   high-speed path is never entered -- which is why it never showed up here.
?pokey_idle
        lda #$13
        sta p_SKCTL
        lda $10                     ; POKMSK (zp) = the OS shadow of IRQEN
        sta p_IRQEN
        rts

?send_chk                           ; send A, fold into checksum $34
        pha
        clc
        adc $34
        adc #$00
        sta $34
        pla
?send_raw                           ; send A as-is
        pha
        jsr ?wait_txr
        pla
        sta p_SEROUT
        rts

?wait_txr                           ; wait serial-output-ready, then reset it.
        ldy #$00                    ; BOUNDED: this loop had NO exit -- any POKEY
?wl     ldx #$00                    ;   hiccup wedged the machine forever with
?wl2    lda #$10                    ;   "LOADING..." already on screen.  ~65 k polls
        bit p_IRQST                 ;   >> one byte time at any speed on any CPU; on
        beq ?wrdy                   ;   timeout fall through, the drive then does not
        dex                         ;   ACK and ?get fails down the normal path.
        bne ?wl2
        dey
        bne ?wl
?wrdy   lda #$ef
        sta p_IRQEN
        lda #$10
        sta p_IRQEN
        rts

?get                                ; receive 1 byte. C=0 ok (A=byte) / C=1 timeout
        ; The timeout is counted in VBLANKS (RTCLOK3, bumped by the OS VBI, which is
        ; an NMI and so keeps ticking under our sei), NOT in loop iterations. It used
        ; to be a 64K-iteration countdown = ~370 ms on a 1.77 MHz 6502 but only ~34 ms
        ; on a 20 MHz Rapidus -- and the drive needs tens of ms for ACK/COMPLETE. So on
        ; Rapidus the read bailed mid-frame, read_one immediately issued a fresh command
        ; while the drive was still transmitting, SIO desynced and the part streamed in
        ; corrupt. Wall-clock timing makes both CPUs wait the same ~240 ms.
        lda RTCLOK3
        adc #11                     ; deadline = now + ~11 ticks (~230 ms); the carry-in
        tax                         ;   is unknown, so +-1 tick -- irrelevant here.
?gl     lda #$20                    ;   X is dead across ?get (callers use Y), and ?grdy
        bit p_IRQST                 ;   reloads it, so the deadline can live there.
        beq ?grdy
        cpx RTCLOK3
        bne ?gl
        sec
        rts
?grdy   ldx #$df                    ; reset SERIN IRQ, read the byte
        stx p_IRQEN
        ldx #$20
        stx p_IRQEN
        lda p_SERIN
        clc
        rts
.endp

        ert *>$0F80                 ; aw_raster's adv_edges1 owns $0F80-$0FBA
        org diskio_resume           ; --- back into the $B400 block ---

;=============================================================================
; stream_to_vram : load dk_cnt sectors from dk_sec into VRAM, base bank in A.
;   Reads up to 128 sectors (one 16K bank) at a time through the MEMAC-B window.
;=============================================================================
.proc stream_to_vram
        sta dk_bank
?bank   lda dk_cnt                  ; done when no sectors left
        ora dk_cnt+1
        beq ?done
        lda dk_cnt+1                ; n = min(dk_cnt, 128)
        bne ?full
        lda dk_cnt
        cmp #129
        bcc ?lt
?full   lda #128
        bne ?setn
?lt     lda dk_cnt
?setn   sta dk_n
        lda dk_bank                 ; select the target bank, point SIO at the window
        ora #$80
        sta VBXE_MEMAC_B
        sta memb_cur                ; keep the poly/pl_byte bank cache consistent
        lda #<DATAW
        sta DBUFLO
        lda #>DATAW
        sta DBUFHI
        lda dk_sec
        sta DAUX1
        lda dk_sec+1
        sta DAUX2
        ldx dk_n
        jsr read_sectors
        bcs ?err
        lda dk_sec                  ; sec += n
        clc
        adc dk_n
        sta dk_sec
        bcc ?ns
        inc dk_sec+1
?ns     lda dk_cnt                  ; cnt -= n
        sec
        sbc dk_n
        sta dk_cnt
        bcs ?nc
        dec dk_cnt+1
?nc     inc dk_bank                 ; next bank
        jmp ?bank
?done   clc
        rts
?err    sec
        rts
.endp

;=============================================================================
; load_part : load part INDEX (X) from the ATR -> VRAM banks + palette RAM.
;   IRQ is enabled only for the duration of the SIO transfers.
;=============================================================================
.proc load_part
        stx dk_idx
        jsr cc_invalidate           ; new part = new shapes -> wipe the cell cache
        jsr cc_init_arenas          ; register this part's 3 region-remainder arenas
        ldx dk_idx                  ; (cc_init_arenas used X) -> restore part index
.ifdef HIRES_CAP
        cpx #GAME_NPARTS-1          ; part idx 8 = access-code (16008) -> SR 320 (readable);
        bne ?lrm                    ;   every other part -> LR 160 (gameplay speed)
        lda #1
        jsr set_render_mode
        jmp ?modeok
?lrm    lda #0
        jsr set_render_mode
?modeok ldx dk_idx                  ; set_render_mode clobbered X -> restore the part index
.endif
        ; --- show a "LOADING..." screen for the duration of the SIO read (the stream
        ;     below freezes the picture for ~1-3 s). Drawn AFTER set_render_mode so it
        ;     matches the mode the player will see. Skipped on an ESC-resume: there the
        ;     saved scene pages are about to be restored ONTO this same page, so it must
        ;     NOT be blanked. (draw_loading touches only the blitter + text scratch, not
        ;     the MEMAC-B window, so it is safe before the stream sets the window up.) ---
        lda code_return
        bne ?noload
        jsr draw_loading
?noload ldx dk_idx                  ; draw_loading clobbered X -> restore the part index
        lda #0                      ; stop any playing SFX: its VRAM is about to be
        sta snd_active              ;   overwritten, and the Timer-1 IRQ must NOT touch
        lda POKMSK                  ;   MEMAC-B while SIO streams through the window
        and #$FE
        sta POKMSK
        sta IRQEN
        cli                         ; SIOV needs the serial IRQ
        ; --- NO high-speed negotiation. ------------------------------------------
        ; The $3F poll and the POKEY-level reader are the ONLY thing this loader does
        ; that the boot loader ($0700, plain SIOV) does not -- and the boot loader
        ; demonstrably reads the whole xex fine on the very machine where the part
        ; stream dies right after "LOADING..." appears. They are also the only code
        ; that behaves differently on a device that answers $3F (SIO2SD does,
        ; SIDE3/AVG/SUB do not, Altirra with accelerated SIO does not) -- i.e. dead
        ; code on every machine this was developed on and live code on exactly the
        ; one that hangs. On a machine with a high-speed SIO patch in the OS there is
        ; nothing to win here anyway: SIOV is already fast.
        lda #$01                    ; $01 = "no high speed" -> read_one takes ?std
        sta hs_div                  ;        -> std_read_one -> stock SIOV
    .ifdef LOAD_DEBUG               ; -d:LOAD_DEBUG=1 tints "LOADING..." per stage, so
        lda #1                      ;   a hang says WHICH stream it died in. OFF in a
        jsr ld_tint                 ;   normal build -- the text just stays white.
    .endif
        ; --- video1 -> banks $14 ---
        ldx dk_idx
        lda atr_v1_sec_lo,x
        sta dk_sec
        lda atr_v1_sec_hi,x
        sta dk_sec+1
        lda atr_v1_cnt_lo,x
        sta dk_cnt
        lda atr_v1_cnt_hi,x
        sta dk_cnt+1
        lda #POLY_BANK0
        jsr stream_to_vram
    .ifdef LOAD_DEBUG
        lda #2
        jsr ld_tint
    .endif
        ; --- bytecode -> banks $18 ---
        ldx dk_idx
        lda atr_code_sec_lo,x
        sta dk_sec
        lda atr_code_sec_hi,x
        sta dk_sec+1
        lda atr_code_cnt_lo,x
        sta dk_cnt
        lda atr_code_cnt_hi,x
        sta dk_cnt+1
        lda #PLAY_BANK0
        jsr stream_to_vram
    .ifdef LOAD_DEBUG
        lda #3
        jsr ld_tint
    .endif
        ; --- video2 -> banks $1C (skip if this part has none) ---
        ldx dk_idx
        lda atr_v2_cnt_lo,x
        ora atr_v2_cnt_hi,x
        beq ?nov2
        ldx dk_idx
        lda atr_v2_sec_lo,x
        sta dk_sec
        lda atr_v2_sec_hi,x
        sta dk_sec+1
        lda atr_v2_cnt_lo,x
        sta dk_cnt
        lda atr_v2_cnt_hi,x
        sta dk_cnt+1
        lda #POLY_BANK0+8
        jsr stream_to_vram
?nov2
    .ifdef LOAD_DEBUG
        lda #4
        jsr ld_tint
    .endif
        ; --- palette -> RAM pal_data ($9000) ---
        ldx dk_idx
        lda atr_pal_sec_lo,x
        sta DAUX1
        lda atr_pal_sec_hi,x
        sta DAUX2
        lda #<pal_data
        sta DBUFLO
        lda #>pal_data
        sta DBUFHI
        ldx dk_idx
        lda atr_pal_cnt,x
        tax
        jsr read_sectors
    .ifdef LOAD_DEBUG
        lda #5
        jsr ld_tint
    .endif
        ; --- this part's full SFX set -> the snd_blist banks + select its dir slice ---
        ldx dk_idx
        lda snd_pdir_start,x
        sta cur_dir_start
        lda snd_pdir_cnt,x
        sta cur_dir_cnt
        jsr load_sounds
    .ifdef LOAD_DEBUG
        lda #6                      ; stage 6 = whole part in, back to white
        jsr ld_tint
    .endif
        sei                         ; back to IRQ-off for the VM
        rts
.endp

;=============================================================================
; load_sounds : stream part dk_idx's sound blob (atr_snd_sec/cnt) from the ATR
;   across the 7 NON-contiguous snd_blist VRAM banks ($0E,$0F,$11,$12,$13,$1E,$1F)
;   -- one bank (<=128 sectors) per chunk, since they are not consecutive. IRQ
;   is already enabled (called inside load_part's cli region).
;=============================================================================
.proc load_sounds
        ldx dk_idx
        lda atr_snd_cnt_lo,x
        sta ls_rem
        lda atr_snd_cnt_hi,x
        sta ls_rem+1
        ora ls_rem
        beq ?done                   ; no sounds for this part
        lda atr_snd_sec_lo,x
        sta dk_sec
        lda atr_snd_sec_hi,x
        sta dk_sec+1
        ldx #0                      ; snd_blist index
?loop   lda ls_rem
        ora ls_rem+1
        beq ?done
        lda ls_rem+1                ; n = min(ls_rem, 128)
        bne ?full
        lda ls_rem
        cmp #129
        bcc ?setn
?full   lda #128
?setn   sta dk_cnt
        sta ls_n
        lda #0
        sta dk_cnt+1
        stx ls_bi                   ; stream_to_vram clobbers X
        lda snd_blist,x
        and #$7F                    ; bare bank (stream_to_vram re-ORs $80)
        jsr stream_to_vram          ; streams dk_cnt sec from dk_sec; advances dk_sec
        sec
        lda ls_rem
        sbc ls_n
        sta ls_rem
        lda ls_rem+1
        sbc #0
        sta ls_rem+1
        ldx ls_bi
        inx
        jmp ?loop
?done   rts
ls_rem  dta a(0)
ls_n    dta 0
ls_bi   dta 0
.endp

;=============================================================================
; load_bitmap : stream a decoded background bitmap (an LR page = 250 sectors,
;   pre-decoded on the PC) from the ATR into VRAM framebuffer PAGE 0 ($000000 =
;   MEMAC-B bank 0). luxe etc. op_memlist a bitmap, then copyPage(0 -> display)
;   to show it; the runtime palette (op_setpal) colours the indices.  A = bitmap
;   table index. The VM's next pl_byte/poly_fetch re-owns its bank after this.
;=============================================================================
.proc load_bitmap
        tax
        lda atr_bmp_sec_lo,x
        sta dk_sec
        lda atr_bmp_sec_hi,x
        sta dk_sec+1
        lda atr_bmp_cnt,x           ; <= 255 sectors -> high byte 0
        sta dk_cnt
        lda #0
        sta dk_cnt+1
        cli                         ; SIOV needs the serial IRQ
        lda #0                      ; VRAM bank 0 = framebuffer page 0
        jsr stream_to_vram
        sei
        rts
.endp
