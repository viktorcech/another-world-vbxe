;=============================================================================
; Exit paths (placed away from the dispatch so its beq targets stay in range)
;=============================================================================
; VBXE not present : restore a normal Atari screen, print a message, exit to DOS.
no_vbxe
        lda #$22
        sta SDMCTL                  ; ANTIC playfield DMA back on
        lda #0
        sta COLOR4
        sta COLBK
        cli
        ldx #0                      ; IOCB #0 : OPEN "E:" read+write
        lda #3
        sta ICCOM
        lda #<nv_edev
        sta ICBAL
        lda #>nv_edev
        sta ICBAH
        lda #$0C
        sta ICAX1
        lda #0
        sta ICAX2
        jsr CIOV
        ldx #0                      ; IOCB #0 : PUT RECORD (print the message)
        lda #$09
        sta ICCOM
        lda #<nv_msg
        sta ICBAL
        lda #>nv_msg
        sta ICBAH
        lda #<nv_len
        sta ICBLL
        lda #>nv_len
        sta ICBLH
        jsr CIOV
?halt   jmp ?halt                   ; halt with the message on screen (NOT jmp
                                    ; (DOSVEC): with no DOS that lands in self-test
                                    ; and instantly wipes the message)
nv_edev dta c'E:',$9B
nv_msg  dta c'VBXE NOT DETECTED!',$9B
nv_len  equ *-nv_msg

; intro finished OR ESC pressed.
;   Disk build (build.ps1 -- it passes -d:GAME_SEC on BOTH passes): hand control
;   back to the RESIDENT boot loader ($0700, src_game/bootloader.asm -- it
;   survives the whole intro), pointed at the game's first sector -> chain-loads
;   awgame.xex.
;   Assembled by hand with no GAME_SEC define: restore the OS screen and return
;   to DOS, i.e. awintro.xex stands on its own.
; src_game/bootloader.asm symbol addresses. These MUST match the assembled loader.
; The Atari boot protocol jumps to load_address+6, so $0706 is boot_init's CODE and
; the variables live at the END of the loader. An earlier revision of these three
; equates assumed the opposite (vars right behind the header) -- the chain then wrote
; cur_sec/buf_pos straight over `lda #$00 / sta SDMCTL` and jumped into the middle of
; `sta $D400`, so the game never came up after the intro. tools/make_full_atr.py now
; re-derives all three from the assembled boot.bin and fails the build on a mismatch,
; so this can never silently drift again.
BOOT_CURSEC equ $07F2               ; bootloader.asm: cur_sec (word)
BOOT_BUFPOS equ $07F4               ;   buf_pos (128 = force a fresh sector read)
BOOT_INIT   equ $0706               ;   boot_init (re-skips the XEX $FF $FF header)
intro_done
.ifdef GAME_SEC
        jsr snd_stop                ; mute + VIMIRQ back to the OS (SIO needs it;
                                    ;   the loader overwrites snd_irq's RAM)
        sei
        lda #0
        sta VBXE_VCTL               ; VBXE overlay OFF -> normal ANTIC/GTIA display
        sta VBXE_MEMAC_CTL          ; MEMAC-A window OFF -> RAM back at $8000
        sta VBXE_MEMAC_B            ; MEMAC-B data window OFF -> RAM back at $4000-$7FFF
        lda #<GAME_SEC
        sta BOOT_CURSEC
        lda #>GAME_SEC
        sta BOOT_CURSEC+1
        lda #128
        sta BOOT_BUFPOS
        cli
        jmp BOOT_INIT               ; loader keeps the screen off and parses awgame.xex
.else
        jsr snd_stop                ; mute + VIMIRQ back to the OS before DOS
        sei                         ;   reclaims the RAM snd_irq lives in
        lda #0
        sta VBXE_VCTL               ; VBXE overlay OFF -> normal ANTIC/GTIA display
        sta VBXE_MEMAC_CTL          ; MEMAC-A window OFF -> RAM back at $8000
        sta VBXE_MEMAC_B            ; MEMAC-B data window OFF -> RAM back at $4000-$7FFF
        lda #$22
        sta SDMCTL                  ; normal playfield DMA (OS VBI copies -> DMACTL)
        cli
        jmp (DOSVEC)                ; hand control back to the resident DOS
.endif
