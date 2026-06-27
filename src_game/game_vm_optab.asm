;=============================================================================
; game_vm_optab.asm  --  the opcode jump table (the VM's dispatch directory).
;
;   27 words, one per opcode 0x00..0x1A, each the address of its handler over in
;   game_vm_ops1 / ops2 / ops3.asm. vm_run_thread (game_vm_sched.asm) reads
;   opcode*2 from here and pokes the address straight into a `jmp` operand -- the
;   self-modifying dispatch. This is the natural LAST file of the split: every
;   handler label it lists is already assembled in the files above.
;
;   Part of the game_vm split.
;=============================================================================

;=============================================================================
; opcode jump table (0x00..0x1A)
;=============================================================================
vm_optab
        .word op_movconst, op_mov, op_add, op_addconst, op_call, op_ret
        .word op_yield, op_jmp, op_install, op_djnz, op_condjmp, op_setpal
        .word op_resettask, op_selpage, op_fillpage, op_copypage, op_updatedisplay
        .word op_remove, op_drawstring, op_sub, op_and, op_or, op_shl, op_shr
        .word op_sound, op_memlist, op_music
