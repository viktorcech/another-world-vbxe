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

; intro finished : restore a normal Atari state and return to DOS.
intro_done
        sei
        lda #0
        sta VBXE_VCTL               ; VBXE overlay OFF -> normal ANTIC/GTIA display
        sta VBXE_MEMAC_CTL          ; MEMAC-A window OFF -> RAM back at $8000
        sta VBXE_MEMAC_B            ; MEMAC-B data window OFF -> RAM back at $4000-$7FFF
        lda #$22
        sta SDMCTL                  ; normal playfield DMA (OS VBI copies -> DMACTL)
        cli
        jmp (DOSVEC)                ; hand control back to the resident DOS
