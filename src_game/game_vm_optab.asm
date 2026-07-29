;=============================================================================
; game_vm_optab.asm  --  the opcode dispatch tables (the VM's dispatch directory).
;
;   PERF (2026-07-02 fps wave): the old 27-entry .word table + `asl` + range
;   check is now a 64-entry SPLIT lo/hi pair (vm_oplo / vm_ophi) indexed by the
;   opcode byte DIRECTLY at vm_fetch (game_vm_sched.asm):
;     * no `asl` (the index IS the opcode),
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
        ert *>$1F80                 ; the two tables are exactly 128 B ($1F00-$1F7F)

        org vm_optab_resume         ; back to the $2000 chain for whatever follows
