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
.proc read_sectors
        stx rs_cnt
?lp     jsr read_one                ; high speed if available, else stock SIOV
        bcs ?err
        lda DBUFLO                  ; dest += 128
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
?err    sec
        rts
rs_cnt  dta 0
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
; std_read_one : stock OS SIOV read of one 128-byte sector (DAUX -> DBUF).
;-----------------------------------------------------------------------------
.proc std_read_one
        lda #$31
        sta DDEVIC
        lda #$01
        sta DUNIT
        lda #$52                    ; read sector
        sta DCOMND
        lda #$40                    ; receive data
        sta DSTATS
        lda #128
        sta DBYTLO
        lda #0
        sta DBYTHI
        lda #$0F
        sta DTIMLO
        jsr SIOV
        bmi ?err
        clc
        rts
?err    sec
        rts
.endp

;-----------------------------------------------------------------------------
; hs_poll : ask D1: for its high-speed SIO divisor (command $3F). Stores the
;   POKEY AUDF3 index in hs_div (2..$27 = usable), else $01 (none). Stock SIOV,
;   one command frame -- negligible vs the part stream. Drives without high
;   speed NAK $3F -> hs_div=$01 -> everything stays stock (unchanged behaviour).
;-----------------------------------------------------------------------------
.proc hs_poll
        lda #$31
        sta DDEVIC
        lda #$01
        sta DUNIT
        lda #$3f                    ; poll high-speed index
        sta DCOMND
        lda #$40                    ; receive 1 byte
        sta DSTATS
        lda #<hs_pbuf
        sta DBUFLO
        lda #>hs_pbuf
        sta DBUFHI
        lda #1
        sta DBYTLO
        lda #0
        sta DBYTHI
        lda #$0F
        sta DTIMLO
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
?txc    lda #$08
        bit p_IRQST
        bne ?txc

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

        lda #$13
        sta p_SKCTL
        plp
        clc
        rts

?fail   lda #$13
        sta p_SKCTL
        plp
        sec
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

?wait_txr                           ; wait serial-output-ready, then reset it
        lda #$10
?wl     bit p_IRQST
        bne ?wl
        lda #$ef
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
        jsr hs_poll                 ; negotiate high-speed SIO once for this load
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
?nov2   ; --- palette -> RAM pal_data ($9000) ---
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
        ; --- this part's full SFX set -> the snd_blist banks + select its dir slice ---
        ldx dk_idx
        lda snd_pdir_start,x
        sta cur_dir_start
        lda snd_pdir_cnt,x
        sta cur_dir_cnt
        jsr load_sounds
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
