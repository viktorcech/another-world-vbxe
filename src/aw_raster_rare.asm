;=============================================================================
; aw_raster_rare.asm - fill_poly_int's inline edge-step code, the RARE half: dy == 1
;   (step = |dx| << 16) and the negative steps. Out of line and out of the main code
;   block ($2000-$3FFF is full); each entry jumps back to fill_poly_int.st_rdn / st_ldn.
;   Generated with the inline half by tools/_gen_segsteps.py. Shared by the game
;   (src_game/aw_raster.asm) and the intro (src/aw_raster.asm).
;=============================================================================
.if 1                                   ; (skill_verify's old build has no fill_poly_int.st_*)
.proc fpi_rare
; right edge, out of line: dy == 1 and the negative steps
rh1    bit dvsign                  ; dy == 1 : step = |dx| << 16
        bmi rn1
        lda #0                      ; (the same bytes, and C = 0 at the end, as the
        sta fill_poly_int.smc_cr0+1    ;   general chain gives)
        sta fill_poly_int.smc2_cr0+1
        sta fill_poly_int.smc_cr1+1
        sta fill_poly_int.smc2_cr1+1
        sta fill_poly_int.smc_cr3+1
        sty fill_poly_int.smc_cr2+1
        tya
        asl @
        sta fill_poly_int.smc2_cr2+1
        lda #0
        rol @
        sta fill_poly_int.smc2_cr3+1
        jmp fill_poly_int.st_rdn
rn1    lda #0
        sta g_N0
        sta N1
        sty g_N2
rng    sec                         ; step = 0 - N (32-bit, borrow-chained)
        lda #0
        sbc g_N0
        sta fill_poly_int.smc_cr0+1
        lda #0
        sbc N1
        sta fill_poly_int.smc_cr1+1
        lda #0
        sbc g_N2
        sta fill_poly_int.smc_cr2+1
        lda #0
        sbc #0
        sta fill_poly_int.smc_cr3+1
.if 1
pbh_1   dta $2C                     ; SMC OPCODE (poly_bcb_h, pbh_set): $2C bit =
        dta a(fill_poly_int.st_rdn) ;   half, fall into the smc2 doubling; $4C jmp
                                    ;   = full, skip it (the operand stays fixed)
.else
        lda poly_bcb_h              ; half mode: step*2 too (the full-detail loop
        jeq fill_poly_int.st_rdn                  ;   never reads the smc2 chain)
.endif
        lda fill_poly_int.smc_cr0+1
        asl @
        sta fill_poly_int.smc2_cr0+1
        lda fill_poly_int.smc_cr1+1
        rol @
        sta fill_poly_int.smc2_cr1+1
        lda fill_poly_int.smc_cr2+1
        rol @
        sta fill_poly_int.smc2_cr2+1
        lda fill_poly_int.smc_cr3+1
        rol @
        sta fill_poly_int.smc2_cr3+1
        jmp fill_poly_int.st_rdn
; left edge, out of line: dy == 1 and the negative steps
lh1    bit dvsign                  ; dy == 1 : step = |dx| << 16
        bmi ln1
        lda #0                      ; (the same bytes, and C = 0 at the end, as the
        sta fill_poly_int.smc_cl0+1    ;   general chain gives)
        sta fill_poly_int.smc2_cl0+1
        sta fill_poly_int.smc_cl1+1
        sta fill_poly_int.smc2_cl1+1
        sta fill_poly_int.smc_cl3+1
        sty fill_poly_int.smc_cl2+1
        tya
        asl @
        sta fill_poly_int.smc2_cl2+1
        lda #0
        rol @
        sta fill_poly_int.smc2_cl3+1
        jmp fill_poly_int.st_ldn
ln1    lda #0
        sta g_N0
        sta N1
        sty g_N2
lng    sec                         ; step = 0 - N (32-bit, borrow-chained)
        lda #0
        sbc g_N0
        sta fill_poly_int.smc_cl0+1
        lda #0
        sbc N1
        sta fill_poly_int.smc_cl1+1
        lda #0
        sbc g_N2
        sta fill_poly_int.smc_cl2+1
        lda #0
        sbc #0
        sta fill_poly_int.smc_cl3+1
.if 1
pbh_2   dta $2C                     ; SMC OPCODE (poly_bcb_h, pbh_set): $2C bit =
        dta a(fill_poly_int.st_ldn) ;   half, fall into the smc2 doubling; $4C jmp
                                    ;   = full, skip it (the operand stays fixed)
.else
        lda poly_bcb_h              ; half mode: step*2 too (the full-detail loop
        jeq fill_poly_int.st_ldn                  ;   never reads the smc2 chain)
.endif
        lda fill_poly_int.smc_cl0+1
        asl @
        sta fill_poly_int.smc2_cl0+1
        lda fill_poly_int.smc_cl1+1
        rol @
        sta fill_poly_int.smc2_cl1+1
        lda fill_poly_int.smc_cl2+1
        rol @
        sta fill_poly_int.smc2_cl2+1
        lda fill_poly_int.smc_cl3+1
        rol @
        sta fill_poly_int.smc2_cl3+1
        jmp fill_poly_int.st_ldn
.endp
.endif
