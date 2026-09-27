; op_resettask -- COLD in steady state (fires on level-section transitions, 0
; execs/frame in the whole-game profile), so it lives OUT of the $2000 hot chain:
; dispatched through vm_optab (a jmp -- the cold path pays nothing), assembled at
; the end of game_diskio's $0DB0 section, inside the guarded $0DB0-$0F7F region.
; (6502-cycles-layout: a rare block that ends in its own jmp/rts always pays.)
.if 1
        nocross op_resettask, rt_a, rt_e   ; the range tests and both loops in one page
.endif
op_resettask                         ; 0x0C : reset/pause a thread range
.if 1
        mfetch0x sfw14
sfr14
.else
        mfetch0
.endif
        sta vm_s1                   ; first
.if 1
        mfetch0x sfw15
sfr15
.else
        mfetch0
.endif
        sta vm_s2                   ; last
.if 1
        mfetch0x sfw16
sfr16
.else
        mfetch0
.endif
        sta vm_op                   ; typ
        lda vm_s2                   ; (zp reload, 3 cyc -- cheaper than tax/txa on a 6502)
        cmp vm_s1
        bcc ?rtdone                 ; last < first -> nothing
rt_a    lda #1
        sta req_any                 ; requests are pending -> next apply scan runs
        ldx vm_s1
.if 1
        lda vm_op                   ; typ is loop-invariant: pick the loop once
        cmp #2
        beq ?rtrem
?rtp    sta tpreq,x                 ; tpause_req = typ, threads first..last
        cpx vm_s2                   ;   (C = X >= last; inx keeps it)
        inx
        bcc ?rtp
        jmp vm_fetch
?rtrem  lda #$FE                    ; typ 2 : remove (treq = $FFFE)
        sta treq_lo,x
        lda #$FF
        sta treq_hi,x
        cpx vm_s2
        inx
        bcc ?rtrem
.else
?rtloop lda vm_op
        cmp #2
        bne ?pause
        lda #$FE                    ; typ 2 : remove (treq = $FFFE)
        sta treq_lo,x
        lda #$FF
        sta treq_hi,x
        jmp ?rtnext
?pause  lda vm_op                   ; else : tpause_req = typ
        sta tpreq,x
?rtnext cpx vm_s2
        beq ?rtdone
        inx
        jmp ?rtloop
.endif
?rtdone jmp vm_fetch
rt_e
.if 1
sfw14   jsr pl_wrap
        jmp sfr14
sfw15   jsr pl_wrap
        jmp sfr15
sfw16   jsr pl_wrap
        jmp sfr16
.endif
        ert *>$0F7F                 ; must stay inside the $0DB0-$0F7F region
