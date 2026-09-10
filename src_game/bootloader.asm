;==============================================
; DOOM2D - Custom ATR Boot Loader
; Loaded to $0700 by Atari OS from boot sectors 1-3
; Parses XEX binary from raw ATR sectors via SIO
;==============================================

        opt h+
        opt o+

        org $0700

; === Boot header (6 bytes, read by OS) ===
        dta $00                 ; boot flag
        dta 3                   ; load 3 sectors (384 bytes)
        dta a($0700)            ; load address
        dta a(boot_init)        ; init address (jump here after load)


; Zero page pointer for indirect store (must be in ZP)
zp_dest     = $E0              ; 2 bytes: destination pointer (free ZP area)

; Moved $0800 -> $0880 to make room for read_sec's retry loop: the OS loads 3
; boot sectors = 384 B to $0700-$087F, so the loader itself may grow to $087F,
; and $0880-$08FF is free RAM in BOTH xex files.
SECBUF      = $0880             ; 128-byte sector read buffer

; Sectors per one percent step of the "LOADING... nn%" counter. build.ps1 passes
; the real value (ceil(intro_sectors/100)) once the intro's size is known; the
; default only serves ad-hoc assembly. Too SMALL a value overflows past 99%.
.ifndef PCT_STEP
PCT_STEP equ 30
.endif

; === Boot entry point ===
boot_init
        jsr vbxe_check          ; no VBXE/FX core -> message + halt NOW, ~1 s after
                                ;   power-on, instead of after the whole intro load
        lda #0
        sta $41                 ; SOUNDR = 0: SIOV reads it per call, so this one
                                ;   store silences the whole multi-minute load buzz
        ; The OS boot screen STAYS ON while the xex streams in (intro and game both
        ; kill ANTIC DMA themselves in their init) showing "LOADING... nn%" top-left.
        ; The intro->game chain re-enters boot_init with the screen already dark
        ; (aw_replayer zeroed SDMCTL) and SAVMSC pointing into RAM the game xex is
        ; loaded OVER -- so every draw, here and in read_sec, is gated on SDMCTL.
        lda $22F                ; SDMCTL: $22 = first boot, 0 = dark chain load
        beq ld_dark
        ldy #LMSG_L-1
ld_cp   lda load_msg,y
        sta ($58),y             ; (SAVMSC) = top-left of screen RAM
        dey
        bpl ld_cp
ld_dark

        ; Skip $FF $FF XEX header
        jsr get_byte
        jsr get_byte

; === Parse next segment ===
parse_seg
        jsr get_byte
        sta seg_lo
        jsr get_byte
        sta seg_hi

        ; Check $FF $FF separator
        lda seg_lo
        and seg_hi
        cmp #$FF
        beq parse_seg           ; skip $FF $FF, re-read

        ; Read end address
        jsr get_byte
        sta end_lo
        jsr get_byte
        sta end_hi

        ; Check INIT vector ($02E2)
        lda seg_hi
        cmp #$02
        bne data_seg
        lda seg_lo
        cmp #$E2
        beq do_init
        cmp #$E0
        beq do_run

; === Load data segment to memory ===
data_seg
        lda seg_lo
        sta zp_dest
        lda seg_hi
        sta zp_dest+1

?lp     jsr get_byte
        ldy #0
        sta (zp_dest),y
        ; Check dest == end
        lda zp_dest
        cmp end_lo
        bne ?next
        lda zp_dest+1
        cmp end_hi
        beq parse_seg           ; segment done, next
?next   inc zp_dest
        bne ?lp
        inc zp_dest+1
        jmp ?lp

; === INIT segment: load 2-byte address, JSR there ===
do_init
        jsr get_byte
        sta jsr_tgt+1
        jsr get_byte
        sta jsr_tgt+2
        jsr jsr_tgt             ; call INIT routine
        jmp parse_seg

; === RUN segment: load 2-byte address, JMP there ===
do_run
        jsr get_byte
        sta jmp_tgt+1
        jsr get_byte
        sta jmp_tgt+2
jmp_tgt jmp $0000               ; patched with RUN address

; === Indirect JSR (self-modifying) ===
jsr_tgt jmp $0000               ; patched, called via JSR

; === Get next byte from sector buffer ===
get_byte
        ldx buf_pos             ; buf_pos is only ever 0..128, so bit 7 (= N on
        bpl ?ok                 ;   the ldx) set means exactly "buffer used up"
        jsr read_sec
        ldx #0
?ok     lda SECBUF,x
        inx
        stx buf_pos
        rts

; === Read one sector via SIO ===
; SIOV returns the status in Y (N set on error). It used to be thrown away, so on
; real SIO one dropped sector meant the XEX parser silently swallowed 128 STALE
; bytes out of SECBUF -- a random hole anywhere in the loaded program.
; NOTE: read_sec's local labels are rd_* on purpose. This file has no .proc, so a
; `?ok` here would silently merge with get_byte's `?ok`.
RD_TRY      = 8
read_sec
        lda #RD_TRY
        sta rd_left
rd_lp   lda #$31
        sta $0300               ; DDEVIC (disk)
        lda #$01
        sta $0301               ; DUNIT (drive 1)
        lda #$52
        sta $0302               ; DCOMND (read)
        lda #$40
        sta $0303               ; DSTATS (receive)
        lda #<SECBUF
        sta $0304               ; DBUFLO
        lda #>SECBUF
        sta $0305               ; DBUFHI
        lda #$0F
        sta $0306               ; DTIMLO (timeout)
        lda #128
        sta $0308               ; DBYTLO
        lda #0
        sta $0309               ; DBYTHI
        lda cur_sec
        sta $030A               ; DAUX1 (sector lo)
        lda cur_sec+1
        sta $030B               ; DAUX2 (sector hi)
        jsr $E459               ; SIOV -> Y = status, N set on error
        bpl rd_ok               ; success: the buffer really holds this sector
        dec rd_left
        bne rd_lp               ; transient -> re-read the SAME sector
        inc $D01A               ; out of tries: tint the border and keep trying --
        lda #RD_TRY             ;   a stalled loader is far less damage than a
        sta rd_left             ;   silently corrupt program image
        bne rd_lp
rd_ok   inc cur_sec
        bne rd_pct
        inc cur_sec+1
rd_pct  lda $22F                ; dark chain load -> (SAVMSC) is game RAM, no draws
        beq rd_done
        dec pct_left            ; one more percent every PCT_STEP sectors
        bne rd_done
        lda #PCT_STEP
        sta pct_left
        ldy #12                 ; "LOADING... 00%" -> units digit at column 12
        lda ($58),y
        clc
        adc #1
        cmp #$1A                ; past screen-code '9' ($19)?
        bcc rd_put
        lda #$10                ; wrap units to '0' (carry stays set for the tens)
        sta ($58),y
        dey
        lda ($58),y
        adc #0                  ; C=1 -> tens digit + 1
rd_put  sta ($58),y
rd_done lda #0
        sta buf_pos
        rts

; === Variables ===
; src/aw_exit.asm pokes cur_sec/buf_pos from the intro through hard-coded
; addresses (BOOT_CURSEC/BOOT_BUFPOS); tools/make_full_atr.py fails the build if
; they drift. Anything added ABOVE moves them.
rd_left     dta 0               ; read_sec retry counter
cur_sec     dta a(4)            ; current sector number (XEX starts at 4)
buf_pos     dta 128             ; position in buffer (128 = force first read)
seg_lo      dta 0
seg_hi      dta 0
end_lo      dta 0
end_hi      dta 0
pct_left    dta PCT_STEP        ; sectors left until the next percent bump; never
                                ;   reset on the chain re-entry -- the dark-screen
                                ;   gate in read_sec skips the whole counter there

; === VBXE FX-core check (feedback: fail FAST, not after minutes of loading) ===
; Mirrors src/aw_vbxe.asm detect_vbxe's $D600 test: CORE_VERSION ($D640) == $10
; (FX core 1.xx) AND (MINOR_REVISION ($D641) & $70) >= $20 (v1.20+). The engine
; only runs at base $D600, so a card strapped to $D700 gets the same message the
; intro would have shown -- just minutes earlier. The copy is unavoidable (this
; runs before one byte of any xex is in RAM); tools/make_full_atr.py compares the
; emitted constants against detect_vbxe's in both xex files and fails the build
; if the two tests ever drift apart. Placed AFTER the variables so the jsr above
; moves cur_sec/buf_pos by only 3 bytes.
; The OS boot screen (E:) is still ON here -- boot_init kills DMA only after this
; returns -- so on failure the text goes straight into screen RAM via SAVMSC,
; top-left on the standard blue boot screen. vc_* labels: no .proc in this file,
; a ?-local here would merge with get_byte's (see read_sec's note above).
vbxe_check
        lda $D640               ; CORE_VERSION
        cmp #$10                ; $10 = FX core 1.xx
        bne vc_no
        lda $D641               ; MINOR_REVISION
        and #$70
        cmp #$20                ; >= v1.20
        bcs vc_ok
vc_no   ldy #VMSG_L-1
vc_cp   lda vbxe_msg,y
        sta ($58),y             ; (SAVMSC) = top-left of screen RAM
        dey
        bpl vc_cp
vc_halt bmi vc_halt             ; dead end (the dey/bpl loop above fell out with
vc_ok   rts                     ;   N=1, and branches keep flags -> loops forever)
vbxe_msg dta d'VBXE NOT DETECTED!'
VMSG_L  equ *-vbxe_msg
load_msg dta d'LOADING... 00%' ; same wording as the game's part-load string;
LMSG_L  equ *-load_msg          ;   read_sec bumps the digits at columns 11/12

        ert *>SECBUF            ; the OS reads 3 boot sectors = the image must
                                ;   end below SECBUF ($0880)
