import aw_pack, game_sim
W=['movconst','mov','add','addconst','call','ret','yield','jmp','install','djnz',
   'condjmp','setpal','resettask','selpage','fillpage','copypage','updatedisplay',
   'remove','drawstring','sub','and','or','shl','shr','sound','memlist','music']
def disasm(part):
    mem=aw_pack.read_memlist(); _,co,_,_=game_sim.MEMLIST_PARTS[part-16000]
    code=aw_pack.load_resource(mem[co])[0]; n=len(code); pc=0; out=[]
    def b():
        nonlocal pc; v=code[pc]; pc+=1; return v
    def w():
        nonlocal pc; v=(code[pc]<<8)|code[pc+1]; pc+=2; return v
    def sw():
        v=w(); return v-0x10000 if v&0x8000 else v
    while pc<n:
        a=pc; op=code[pc]; pc+=1
        if op&0x80:
            off=((op<<8)|b()); xx=b(); yy=b(); out.append((a,f"draw_bg off={off*2&0xffff} @({xx},{yy})")); continue
        if op&0x40:
            s="draw_spr"; off=w(); x=b()
            if not(op&0x20):
                if not(op&0x10): x=(x<<8)|b()
                else: s+=f" x=V{x}"; x=None
            else:
                if op&0x10: x+=256
            if x is not None: s+=f" x={x}"
            y=b()
            if not(op&8):
                if not(op&4): y=(y<<8)|b()
                else: s+=f" y=V{y}"; y=None
            if y is not None: s+=f" y={y}"
            if not(op&2):
                if not(op&1): pass
                else: z=b(); s+=f" z=V{z}"
            else:
                if op&1: pass
                else: z=b(); s+=f" z={z}"
            out.append((a,f"{s} off={off*2&0xffff}")); continue
        if op>26: out.append((a,f"?? {op:02x}")); continue
        nm=W[op]
        if op==0x00: v=b();k=sw(); s=f"V{v} := {k}"
        elif op==0x01: d=b();ss=b(); s=f"V{d} := V{ss}"
        elif op==0x02: d=b();ss=b(); s=f"V{d} += V{ss}"
        elif op==0x03: v=b();k=sw(); s=f"V{v} += {k}"
        elif op==0x04: x=w(); s=f"call {x:04x}"
        elif op==0x05: s="ret"
        elif op==0x06: s="yield"
        elif op==0x07: x=w(); s=f"jmp {x:04x}"
        elif op==0x08: t=b();x=w(); s=f"install T{t}@{x:04x}"
        elif op==0x09: v=b();x=w(); s=f"djnz V{v} {x:04x}"
        elif op==0x0A:
            sub=b();v=b()
            if sub&0x80: rb=b();rhs=f"V{rb}"
            elif sub&0x40: rhs=str(sw())
            else: rhs=str(b())
            dst=w();m=sub&7;cmp=['==','!=','>','>=','<','<=','?','?'][m]
            s=f"if V{v} {cmp} {rhs} -> {dst:04x}"
        elif op==0x0B: x=w(); s=f"setpal {x>>8}"
        elif op==0x0C: f=b();l=b();t=b(); s=f"resettask {f}..{l} t{t}"
        elif op==0x0D: x=b(); s=f"selpage {x}"
        elif op==0x0E: p=b();c=b(); s=f"fillpage {p} c{c}"
        elif op==0x0F: i=b();j=b(); s=f"copypage {i}->{j}"
        elif op==0x10: x=b(); s=f"updatedisplay {x}"
        elif op==0x11: s="remove"
        elif op==0x12: sid=w();x=b();y=b();c=b(); s=f"drawstr 0x{sid:X}@({x},{y})c{c}"
        elif op==0x13: d=b();ss=b(); s=f"V{d} -= V{ss}"
        elif op==0x14: v=b();k=w(); s=f"V{v} &= {k:04x}"
        elif op==0x15: v=b();k=w(); s=f"V{v} |= {k:04x}"
        elif op==0x16: v=b();k=w(); s=f"V{v} <<= {k&15}"
        elif op==0x17: v=b();k=w(); s=f"V{v} >>= {k&15}"
        elif op==0x18: r=w();b();b();b(); s=f"sound {r}"
        elif op==0x19:
            num=w(); s=(f"*MEMLIST part {num-0x3E80+16000}*" if num>=0x3E80 else f"memlist res {num}")
        elif op==0x1A: r=w();w();b(); s=f"music {r}"
        out.append((a,f"{nm}: {s}"))
    return out
if __name__=='__main__':
    import sys
    part=int(sys.argv[1]) if len(sys.argv)>1 else 16004
    for a,s in disasm(part): print(f"{a:04x}  {s}")
