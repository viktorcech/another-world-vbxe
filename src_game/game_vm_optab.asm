;=============================================================================
; game_vm_optab.asm  --  the opcode dispatch tables (the VM's dispatch directory).
;
;   A 64-entry WORD table indexed by 2 * opcode: vm_fetch's `asl` both sorts the
;   draw opcodes out (C = bit 7, N = bit 6) and makes the index (game_vm_sched.asm):
;     * no `cmp #27` guard -- entries $1B..$3F all point at op_remove, which is
;       exactly what the old bad-PC guard did (halt the thread, never wild-jump),
;     * opcode 6 (yield) points straight at vm_exit -- yield has no body.
;   Opcodes $40+ never reach the table (vm_fetch routes them to the draw decoders
;   first), so 64 entries cover the whole reachable range.
;
;   The 128 table bytes live in the $1F00 gap (between game_cellcache's cold
;   block, which ends < $1F00 -- ert-guarded there -- and the $2000 code segment)
;   to keep the tight $2000-$3FFF chain free; `org` returns to the chain after.
;
;   Part of the game_vm split -- the natural LAST file: every handler label it
;   lists is already assembled in the files above.
;=============================================================================
vm_optab_resume equ *               ; current position in the $2000 code chain

        org $1F00                   ; free gap: cc cold block ends < $1F00 (ert'd)
.if 1
vm_optab dta a(op_movconst), a(op_mov), a(op_add), a(op_addconst), a(op_call), a(op_ret)
        dta a(op_yield), a(op_jmp), a(op_install), a(op_djnz), a(op_condjmp), a(op_setpal)
        dta a(op_resettask), a(op_selpage), a(op_fillpage), a(op_copypage), a(op_updatedisplay)
        dta a(op_remove), a(op_drawstring), a(op_sub), a(op_and), a(op_or), a(op_shl), a(op_shr)
        dta a(op_sound), a(op_memlist), a(op_music)
:37     dta a(op_remove)            ; $1B-$3F : invalid -> halt the thread
.else
vm_oplo dta <op_movconst, <op_mov, <op_add, <op_addconst, <op_call, <op_ret
        dta <op_yield, <op_jmp, <op_install, <op_djnz, <op_condjmp, <op_setpal
        dta <op_resettask, <op_selpage, <op_fillpage, <op_copypage, <op_updatedisplay
        dta <op_remove, <op_drawstring, <op_sub, <op_and, <op_or, <op_shl, <op_shr
        dta <op_sound, <op_memlist, <op_music
:37     dta <op_remove              ; $1B-$3F : invalid -> halt the thread
vm_ophi dta >op_movconst, >op_mov, >op_add, >op_addconst, >op_call, >op_ret
        dta >op_yield, >op_jmp, >op_install, >op_djnz, >op_condjmp, >op_setpal
        dta >op_resettask, >op_selpage, >op_fillpage, >op_copypage, >op_updatedisplay
        dta >op_remove, >op_drawstring, >op_sub, >op_and, >op_or, >op_shl, >op_shr
        dta >op_sound, >op_memlist, >op_music
:37     dta >op_remove
.endif
        ert *>$1F80                 ; the word table is exactly 128 B ($1F00-$1F7F)

        org vm_optab_resume         ; back to the $2000 chain for whatever follows
