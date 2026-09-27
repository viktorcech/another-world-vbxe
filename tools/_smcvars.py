"""_smcvars -- the variables that also live as SMC immediate operands (6502 skill: a
variable as the operand of lda # / cmp #). A harness that pokes such a variable into
memory must poke every operand too: poke(mem, L, name, value).

Operand sites are the labels <prefix><n> (n = 1, 2, ...); the operand byte is label+1.
"""
SITES = {
    'poly_bcb_h': 'pbh_',
    'hires': 'hrs_',
}


def poke(mem, L, name, value):
    if name in L:
        mem[L[name]] = value & 0xFF
    pre = SITES.get(name)
    if not pre:
        return
    for k, a in L.items():
        kl = k.lower().split('.')[-1]           # (a site inside a .proc: proc.label)
        if kl.startswith(pre) and kl[len(pre):].isdigit():
            if mem[a] in (0x2C, 0x4C):          # a patched-OPCODE hop (pbh_1/2/3):
                mem[a] = 0x4C if (value & 0xFF) == 0 else 0x2C   # $4C jmp / $2C bit
            else:                               # a patched-operand site (lda # ...)
                mem[a + 1] = value & 0xFF
