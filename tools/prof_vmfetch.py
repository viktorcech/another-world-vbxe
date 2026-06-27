#!/usr/bin/env python3
"""
prof_vmfetch.py - GAME profiler for aw3.txt: inlining the operand fetch in hot opcodes.

After aw2 the bytecode pointer is a ZP running pointer, so an opcode handler can read
its operands with an inline `lda (pl_wlo),y / inc pl_wlo (+ page-wrap)` instead of
`jsr pl_byte`. That drops, per operand byte, the jsr+rts (12 cyc) and the per-call
bank-cache compare (~8 cyc; redundant mid-opcode, where no draw can steal the bank).

This breaks the per-frame bytecode fetches into:
  * OPCODE bytes  -- one per dispatched instruction; fetched in the VM loop, which
                     still needs the bank re-own (a draw may precede it) -> keep pl_byte.
  * OPERAND bytes -- the rest; safely inlinable -> the aw3 win.

It estimates the saving (operand_bytes * ~INLINE_SAVING cyc) per part, so you can judge
whether aw3's extra complexity (an inline fetch macro with page-wrap + bank-cross, and
deriving the saved PC instead of maintaining pl_lo/pl_mid) is worth it after aw2.

Run:   python tools/prof_vmfetch.py            # all parts
       python tools/prof_vmfetch.py 16004 600  # one part, N frames
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_sim
import aw_sim

PARTS = [(16002, "water"), (16003, "jail"), (16004, "cite"),
         (16005, "arene"), (16006, "luxe"), (16007, "final")]
PAL_BUDGET = 35568
INLINE_SAVING = 20                  # ~cyc saved per inlined operand byte (jsr/rts + bank cmp)


def profile_part(part, frames):
    bytes_cnt = [0]                 # total bytecode bytes fetched (b=1, w=2)
    op_cnt = [0]                    # opcodes dispatched (= opcode bytes)
    ob, ow = aw_sim.VM.b, aw_sim.VM.w

    def b(self, _c=bytes_cnt):
        _c[0] += 1; return ob(self)

    def w(self, _c=bytes_cnt):
        _c[0] += 2; return ow(self)

    # count dispatches: wrap every opcode handler + the two draw paths
    base_ops = list(game_sim.GameVM.OPS)
    wrapped = []
    for h in base_ops:
        def mk(h):
            def f(self, _c=op_cnt, _h=h):
                _c[0] += 1; return _h(self)
            return f
        wrapped.append(mk(h))
    odb, ods = game_sim.GameVM.draw_bg, game_sim.GameVM.draw_sprite

    def dbg(self, op, _c=op_cnt):
        _c[0] += 1; return odb(self, op)

    def dsp(self, op, _c=op_cnt):
        _c[0] += 1; return ods(self, op)

    game_sim.GameVM.b = b
    game_sim.GameVM.w = w
    game_sim.GameVM.OPS = wrapped
    game_sim.GameVM.draw_bg = dbg
    game_sim.GameVM.draw_sprite = dsp
    try:
        vm = game_sim.GameVM(part)
        vm.run(frames)
    finally:
        game_sim.GameVM.b = ob
        game_sim.GameVM.w = ow
        game_sim.GameVM.OPS = base_ops
        game_sim.GameVM.draw_bg = odb
        game_sim.GameVM.draw_sprite = ods

    nfr = max(len(vm.frames), 1)
    total = bytes_cnt[0]
    opcodes = op_cnt[0]
    operands = total - opcodes      # bytes that are NOT the opcode byte
    op_pf = operands // nfr
    saved = op_pf * INLINE_SAVING
    return dict(frames=len(vm.frames), total_pf=total // nfr, op_pf=opcodes // nfr,
                operand_pf=op_pf, cyc=saved, budget=100 * saved / PAL_BUDGET,
                operand_frac=(100 * operands / total) if total else 0)


def main():
    print("== VM operand-inline (aw3) profiler ==\n")
    if len(sys.argv) > 1:
        part = int(sys.argv[1]); frames = int(sys.argv[2]) if len(sys.argv) > 2 else 250
        parts = [(part, dict(PARTS).get(part, f"p{part}"))]
    else:
        parts = PARTS; frames = 250

    print(f"per-frame, {frames} no-input frames; operand bytes are the aw3-inlinable ones:\n")
    print(f"  {'part':8}{'opcodes':>8}{'operands':>9}{'oper%':>7}"
          f"{'~cyc/frame':>12}{'% PAL':>8}")
    for p, name in parts:
        r = profile_part(p, frames)
        print(f"  {name:8}{r['op_pf']:8}{r['operand_pf']:9}{r['operand_frac']:6.0f}%"
              f"{r['cyc']:12}{r['budget']:7.1f}%")
    print()
    print("aw3 saves ~20 cyc per OPERAND byte (jsr/rts + the redundant per-fetch bank")
    print("compare). The opcode byte itself stays on pl_byte (it may follow a draw, so it")
    print("must re-own the MEMAC-B bank). Weigh this against the added complexity: an inline")
    print("fetch macro that still handles the 256-byte page wrap + the rare 16K bank cross,")
    print("and deriving the saved PC from the window pointer instead of keeping pl_lo/pl_mid.")


if __name__ == "__main__":
    main()
