#!/usr/bin/env python3
"""_bench_frame.py - boot the REAL awgame.xex on a cycle-counting NMOS 6502 with a
VBXE model and play whole game frames: the VM, the shape decoder, the raster, the
cell cache, the blitter -- nothing stubbed except the OS ROM.

Machine model:
  * 64 KB RAM (BASIC off), OS ROM area reads 0; SIOV ($E459) is a host trap that
    serves 128-byte sectors straight out of awgame_full.atr through the CPU memory
    map (so the MEMAC-B window lands them in VRAM exactly like the real loader).
  * VBXE (as alt-src/Altirra/source/vbxe.cpp models it): 512 KB VRAM, MEMAC-A
    ($D65E/$D65F) and MEMAC-B ($D65D) windows, palette writes, XDLA (page flips),
    and the blitter: BL_ADR + START run the whole BCB list at once (modes 0-6,
    AND/XOR, zoom, pattern, chaining) and BUSY stays set for the blit's cost in
    blitter cycles (21 per BCB +1 when AND = 0, per row dst [+src when AND != 0]
    [+dst again for RMW modes], zero source bytes credited back) at
    (312*912 - 200*160) / (312*114) = 7.1 blitter cycles per CPU cycle.
  * The OS VBI is modelled by its effect only: RTCLOK ($12-$14) +1 every 35568
    cycles (PAL). No IRQs are delivered (the sound IRQ has its own bench,
    tools/_bench_sndirq.py). POKEY: RANDOM (frozen $FF while SKCTL&3 = 0, as the
    covox probe expects), SKSTAT/KBCODE = no key, PORTA/TRIG0 = stick centred.
  * Pure-read spin loops (the busy waits on BL_BUSY and the RTCLOK pacing loops) are
    FAST-FORWARDED to the next event, their cycles are still counted -- per PC.

Per displayed frame (op_updatedisplay) it records: busy cycles (everything except
the pacing spin), blitter-wait cycles, and a hash of the displayed page + palette.

    python tools/_bench_frame.py [--frames N] [--warm W] [--part P] [--pos Q]
                                 [--build DIR] [--top K]
      --build DIR : run DIR/awgame.xex + DIR/awgame_full.atr + DIR/awgame.lst
                    (default: the project root build + out/awgame.lst)
    -> out/bench/bench_<tag>.txt ; the VIDEOSHA line is the bit-identity gate.
"""
import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _sim6502 as S                                              # noqa: E402
import _lst6502 as LS                                             # noqa: E402

FRAME = 312 * 114
BLIT_RATE = (312 * 912 - 200 * 160) / (312 * 114)
SIOV = 0xE459
CIOV = 0xE456
RTCLOK = 0x12


class Machine(S.CPU):
    def __init__(self, xex, atr, lst):
        super().__init__()
        self.vram = bytearray(512 * 1024)
        self.atr = open(atr, 'rb').read()
        self.lst = lst
        self.ins = LS.read('game', lst)
        self.lab = {}
        for i in self.ins:
            for nm in i.labels:
                self.lab.setdefault(nm, i.addr)
        m = self.m
        data = open(xex, 'rb').read()
        p = 0
        self.run_addr = None
        while p + 4 <= len(data):
            lo = data[p] | (data[p + 1] << 8)
            if lo == 0xFFFF:
                p += 2
                continue
            hi = data[p + 2] | (data[p + 3] << 8)
            p += 4
            n = hi - lo + 1
            m[lo:lo + n] = data[p:p + n]
            p += n
        self.run_addr = m[0x2E0] | (m[0x2E1] << 8)
        # OS ROM area: zeros, with host traps at the entry points the game calls
        for a in range(0xC000, 0x10000):
            if not (0xD000 <= a < 0xD800):
                m[a] = 0
        m[SIOV] = 0x02
        m[CIOV] = 0x02
        self.memb = 0
        self.mac_ctl = 0
        self.mac_bank = 0
        self.bl_adr = 0
        self.busy_until = 0
        self.skctl = 3
        self.rng = 0x1234
        self.psel = 0
        self.csel = 0
        self.crgb = [0, 0, 0]
        self.pal = [bytearray(768) for _ in range(4)]
        self.next_vbl = FRAME
        self.nblits = 0
        self.nbcb = 0
        self.blit_cyc_total = 0
        self.flips = []                 # (cycle, page)
        self._pages()
        # spin loops (pure-read, fast-forwardable)
        self.spin_tab = {}
        self._find_spins()
        self.spins = set(self.spin_tab)
        self.pc = self.run_addr
        self.s = 0xFF
        self.i = 1

    # ---------------------------------------------------------------- memory map
    def _pages(self):
        rp, wp = self.rp, self.wp
        for pg in range(256):
            plain = pg < 0xC0 or pg >= 0x100
            rp[pg] = wp[pg] = 1 if plain else 0
        for pg in range(0xD0, 0xD8):
            rp[pg] = wp[pg] = 0
        if self.memb & 0x80:
            for pg in range(0x40, 0x80):
                rp[pg] = wp[pg] = 0
        self.mac_lo = self.mac_hi = 0
        if (self.mac_bank & 0x80) and (self.mac_ctl & 0x08):
            base = (self.mac_ctl & 0xF0) << 8
            lim = min(0x10000, base + (0x1000 << (self.mac_ctl & 3)))
            self.mac_lo, self.mac_hi = base, lim
            for pg in range(base >> 8, lim >> 8):
                rp[pg] = wp[pg] = 0
        self.mac_vbase = (self.mac_bank & (0x7F, 0x7E, 0x7C, 0x78)[self.mac_ctl & 3]) << 12
        self.memb_vbase = (self.memb & 0x1F) << 14

    def _vaddr(self, a):
        if self.mac_lo <= a < self.mac_hi:
            return self.mac_vbase + (a - self.mac_lo)
        if (self.memb & 0x80) and 0x4000 <= a < 0x8000:
            return self.memb_vbase + (a - 0x4000)
        return -1

    def rd(self, a):
        v = self._vaddr(a)
        if v >= 0:
            return self.vram[v]
        if 0xD000 <= a < 0xD800:
            return self.io_rd(a)
        if a >= 0xC000:
            return self.m[a]
        return self.m[a]

    def wr(self, a, v):
        va = self._vaddr(a)
        if va >= 0:
            self.vram[va] = v
            return
        if 0xD000 <= a < 0xD800:
            self.io_wr(a, v)
            return
        if a >= 0xC000:
            return                                  # ROM
        self.m[a] = v

    # ---------------------------------------------------------------- I/O
    def io_rd(self, a):
        hi = a >> 8
        if hi == 0xD6:
            r = a & 0xFF
            if r == 0x40:
                return 0x10
            if r == 0x41:
                return 0x26
            if r == 0x53:
                return 0x02 if self.cyc < self.busy_until else 0x00
            return 0xFF
        if hi == 0xD2:
            r = a & 0x0F
            if r == 0x0A:                         # RANDOM
                if (self.skctl & 3) == 0:
                    return 0xFF
                self.rng = (self.rng * 1103515245 + 12345) & 0x7FFFFFFF
                return (self.rng >> 16) & 0xFF
            if r == 0x09:
                return 0xFF                       # KBCODE
            if r == 0x0F:
                return 0xFF                       # SKSTAT: no key
            if r == 0x0E:
                return 0xFF                       # IRQST: nothing pending
            if r == 0x0C:
                return 0xFF                       # (PokeyMAX ID) = plain POKEY
            return 0xFF
        if hi == 0xD0:
            r = a & 0x1F
            if r == 0x10:
                return 1                          # TRIG0 not pressed
            if r == 0x14:
                return 0x01                       # PAL
            if r == 0x1F:
                return 7
            return 0
        if hi == 0xD3:
            if (a & 3) == 0:
                return 0xFF                       # PORTA: stick centred
            return 0xFF
        if hi == 0xD4:
            if (a & 0x0F) == 0x0B:
                return ((self.cyc % FRAME) // 114) >> 1
            return 0
        return 0xFF

    def io_wr(self, a, v):
        hi = a >> 8
        if hi == 0xD6:
            r = a & 0xFF
            if r == 0x5D:
                self.memb = v
                self._pages()
            elif r == 0x5E:
                self.mac_ctl = v
                self._pages()
            elif r == 0x5F:
                self.mac_bank = v
                self._pages()
            elif r == 0x50:
                self.bl_adr = (self.bl_adr & 0x7FF00) | v
            elif r == 0x51:
                self.bl_adr = (self.bl_adr & 0x700FF) | (v << 8)
            elif r == 0x52:
                self.bl_adr = (self.bl_adr & 0x0FFFF) | ((v & 7) << 16)
            elif r == 0x53:
                if v & 1:
                    if self.cyc >= self.busy_until:
                        self.blit()
                else:
                    self.busy_until = self.cyc
            elif r == 0x41:
                self.flips.append((self.cyc, v >> 6))
            elif r == 0x44:
                self.csel = v
            elif r == 0x45:
                self.psel = v & 3
            elif r == 0x46:
                self.crgb[0] = v
            elif r == 0x47:
                self.crgb[1] = v
            elif r == 0x48:
                self.crgb[2] = v
                p = self.pal[self.psel]
                p[self.csel * 3:self.csel * 3 + 3] = bytes(self.crgb)
                self.csel = (self.csel + 1) & 0xFF
            return
        if hi == 0xD2:
            if (a & 0x0F) == 0x0F:
                self.skctl = v
            return
        if hi == 0xD4:
            if (a & 0x0F) == 0x0A:                # WSYNC
                self.cyc = (self.cyc // 114 + 1) * 114
            return

    # ---------------------------------------------------------------- blitter
    def blit(self):
        vram = self.vram
        adr = self.bl_adr
        cost = 0
        for _ in range(1024):
            b = vram[adr:adr + 21]
            adr = (adr + 21) & 0x7FFFF
            cost += self._one_bcb(b)
            self.nbcb += 1
            if not (b[20] & 0x08):
                break
        self.nblits += 1
        self.blit_cyc_total += cost
        self.busy_until = self.cyc + int(cost / BLIT_RATE + 0.999)

    def _one_bcb(self, b):
        vram = self.vram
        src = b[0] | (b[1] << 8) | (b[2] << 16)
        sy = ((b[3] | (b[4] << 8)) & 0x1FFF)
        sy = sy - 0x2000 if sy & 0x1000 else sy
        sx = b[5] - 256 if b[5] & 0x80 else b[5]
        dst = b[6] | (b[7] << 8) | (b[8] << 16)
        dy = ((b[9] | (b[10] << 8)) & 0x1FFF)
        dy = dy - 0x2000 if dy & 0x1000 else dy
        dx = b[11] - 256 if b[11] & 0x80 else b[11]
        w = b[12] + ((b[13] & 1) << 8) + 1
        h = b[14] + 1
        andm, xorm, colm = b[15], b[16], b[17]
        zx = (b[18] & 7) + 1
        zy = ((b[18] >> 4) & 7) + 1
        pat = b[19]
        mode = b[20] & 7
        # cost per Altirra LoadBCB
        cpr = w * zx
        saved = 0
        if andm:
            cpr += w
        if andm or xorm:
            if mode == 1:
                if colm:
                    cpr += w * zx
                    saved = zx * 2 if andm else zx
                else:
                    saved = zx if andm else 0
            elif mode != 0:
                cpr += w * zx
                saved = zx * 2 if andm else zx
        cost = 21 + (1 if andm == 0 else 0)
        patw = (pat & 0x3F) + 1 if pat & 0x80 else 1 << 30
        zc = 0
        for _row in range(h * zy if zy > 1 else h):
            s = src
            d = dst
            pc = patw
            zeros = 0
            if mode == 0 and zx == 1 and not (pat & 0x80):
                if andm == 0:
                    if dx == 1:
                        dd = d & 0x7FFFF
                        if dd + w <= len(vram):
                            vram[dd:dd + w] = bytes([xorm]) * w
                        else:
                            for x in range(w):
                                vram[(d + x) & 0x7FFFF] = xorm
                    else:
                        for x in range(w):
                            vram[d & 0x7FFFF] = xorm
                            d += dx
                else:
                    for x in range(w):
                        vram[d & 0x7FFFF] = (vram[s & 0x7FFFF] & andm) ^ xorm
                        d += dx
                        s += sx
            elif mode == 0:
                for x in range(w):
                    c = (vram[s & 0x7FFFF] & andm) ^ xorm
                    for _i in range(zx):
                        vram[d & 0x7FFFF] = c
                        d += dx
                    s += sx
                    pc -= 1
                    if not pc:
                        pc = patw
                        s = src
            else:
                for x in range(w):
                    c = (vram[s & 0x7FFFF] & andm) ^ xorm
                    if c:
                        for _i in range(zx):
                            dd = d & 0x7FFFF
                            dv = vram[dd]
                            if mode == 1:
                                vram[dd] = c
                            elif mode == 2:
                                vram[dd] = (c + dv) & 0xFF
                            elif mode == 3:
                                vram[dd] = c | dv
                            elif mode == 4:
                                vram[dd] = c & dv
                            elif mode == 5:
                                vram[dd] = c ^ dv
                            else:
                                lo = (c & 0x0F) or (dv & 0x0F)
                                hi = (c & 0xF0) or (dv & 0xF0)
                                vram[dd] = lo | hi
                            d += dx
                    else:
                        zeros += 1
                        if mode == 4:
                            for _i in range(zx):
                                vram[d & 0x7FFFF] = 0
                                d += dx
                        else:
                            d += dx * zx
                    s += sx
                    pc -= 1
                    if not pc:
                        pc = patw
                        s = src
            if mode != 0 and andm != 0:
                cost -= saved * zeros
            cost += cpr
            dst += dy
            zc += 1
            if zc >= zy:
                zc = 0
                src += sy
        return cost

    # ---------------------------------------------------------------- traps
    def trap(self, pc):
        if pc == SIOV:
            m = self.m
            cmd = m[0x302]
            buf = m[0x304] | (m[0x305] << 8)
            n = m[0x308] | (m[0x309] << 8)
            sec = m[0x30A] | (m[0x30B] << 8)
            ok = False
            if cmd == 0x52 and m[0x300] == 0x31:
                off = 16 + (sec - 1) * 128
                if 1 <= sec and off + 128 <= len(self.atr):
                    blk = self.atr[off:off + 128]
                    for i in range(min(n, 128)):
                        self.wr((buf + i) & 0xFFFF, blk[i])
                    ok = True
            st = 1 if ok else 0x8A
            m[0x303] = st
            self.y = st
            self.zv = self.nv = st
            self.do_rts()
            self.sio_sectors = getattr(self, 'sio_sectors', 0) + 1
            return
        raise S.Halt('trap at $%04X' % pc)

    # ---------------------------------------------------------------- spin loops
    def _find_spins(self):
        """backward branches whose loop body only READS (loads / compares / flag ops),
        and reads at least one location only an EVENT changes (I/O or RTCLOK): the
        CPU state after any whole number of iterations is the same until the next
        VBI tick or blitter end, so they can be fast-forwarded."""
        by_addr = {i.addr: i for i in self.ins if not i.data}
        READ_OK = {'lda', 'ldx', 'ldy', 'cmp', 'cpx', 'cpy', 'bit', 'and', 'ora', 'eor',
                   'adc', 'sbc', 'sec', 'clc', 'nop'}
        for i in self.ins:
            if i.data or i.mn not in LS.BRANCH or i.arg >= i.addr:
                continue
            t = i.arg
            body = []
            a = t
            ok = True
            vol = False
            while a <= i.addr:
                j = by_addr.get(a)
                if j is None:
                    ok = False
                    break
                body.append(j)
                if j is not i:
                    if j.mn in LS.BRANCH:
                        if t <= j.arg <= i.addr:      # a second loop edge -> skip
                            ok = False
                            break
                    elif j.mn not in READ_OK:
                        ok = False
                        break
                    if j.mode in ('zp', 'abs') and j.mn in ('lda', 'ldx', 'ldy', 'cmp', 'cpx', 'cpy',
                                                          'bit', 'and', 'ora', 'eor', 'adc', 'sbc'):
                        if 0xD000 <= j.arg < 0xD800 or RTCLOK <= j.arg <= RTCLOK + 2:
                            vol = True
                    elif j.mode not in ('imp', 'imm', 'zp', 'abs', 'rel'):
                        ok = False
                        break
                a += j.size
            if not ok or not vol:
                continue
            # the loop must (re)load every register it tests: the first A/X/Y use in
            # the body must be a load of that register
            first_a = next((j for j in body if j.mn in ('lda', 'cmp', 'and', 'ora', 'eor', 'adc',
                                                         'sbc', 'bit')), None)
            if first_a is not None and first_a.mn != 'lda' and first_a.mn != 'bit':
                continue
            cost = sum(LS.cycles(j.mn, j.mode) for j in body) + 1      # taken back-edge
            self.spin_tab[t] = (i.addr, body, cost)

    def spin(self, t, cyc):
        br, body, cost = self.spin_tab[t]
        ev = self.next_vbl
        if any(j.arg == 0xD653 for j in body if j.mode == 'abs'):
            # a BL_BUSY wait: runs until the blit ends, never to the next VBLANK (the
            # blit may have ended between the read and this branch: no fast-forward)
            if self.busy_until <= cyc:
                return 0
            ev = min(ev, self.busy_until)
        elif cyc < self.busy_until < ev:
            ev = self.busy_until
        n = (ev - cyc) // cost - 1
        if n <= 0:
            return 0
        if self.prof:
            pc = self.pcyc
            for j in body:
                pc[j.addr] += n * (LS.cycles(j.mn, j.mode) + (1 if j is body[-1] else 0))
            pc[br] -= n * cost                        # the generic add books k on the branch
        return n * cost

    # ---------------------------------------------------------------- run
    def run_until(self, cyc_limit):
        while self.cyc < cyc_limit:
            lim = min(cyc_limit, self.next_vbl)
            self.run(lim)
            if self.cyc >= self.next_vbl:
                self.next_vbl += FRAME
                m = self.m
                v = m[0x14] + 1
                m[0x14] = v & 0xFF
                if v > 0xFF:
                    v = m[0x13] + 1
                    m[0x13] = v & 0xFF
                    if v > 0xFF:
                        m[0x12] = (m[0x12] + 1) & 0xFF


def frame_bench(build_dir=None, frames=20, warm=10, part=None, pos=None, prof=True, verbose=True,
                max_cycles=None):
    if build_dir:
        xex = os.path.join(build_dir, 'awgame.xex')
        atr = os.path.join(build_dir, 'awgame_full.atr')
        lst = os.path.join(build_dir, 'awgame.lst')
    else:
        xex = os.path.join(PROJ, 'awgame.xex')
        atr = os.path.join(PROJ, 'awgame_full.atr')
        lst = os.path.join(PROJ, 'out', 'awgame.lst')
    mc = Machine(xex, atr, lst)
    L = mc.lab
    if part is not None:
        # vm_init: `ldx #GAME_START_PART-GAME_FIRST_PART / jsr load_part` -> patch the immediate
        a = L['vm_init']
        hit = None
        for i in mc.ins:
            if i.mn == 'ldx' and i.mode == 'imm' and 'GAME_START_PART-GAME_FIRST_PART' in i.src:
                hit = i
                break
        assert hit is not None
        mc.m[hit.addr + 1] = part - 16000
    if pos is not None:
        # GAME_START_POS: `lda #<POS / sta var_lo` after vm_reset_threads
        a = L['vm_init']
        for i in mc.ins:
            if i.addr >= a and i.mn == 'sta' and i.arg == 0xB000:
                prev = [j for j in mc.ins if j.addr < i.addr and not j.data][-1]
                if prev.mn == 'lda' and prev.mode == 'imm':
                    mc.m[prev.addr + 1] = pos & 0xFF
                    break
    upd = L['op_updatedisplay']
    # count frames: trap-free -- watch the PC by running to op_updatedisplay in steps
    rec = []
    t0 = time.time()
    frame_marks = []

    class Mark(Exception):
        pass
    # op_updatedisplay's first instruction is `mfetch` (ldy #0): we detect arrival by
    # planting a host trap there and re-executing the original byte after the trap.
    orig = mc.m[upd]
    mc.m[upd] = 0x02
    outer = mc

    def trap(pc, _orig=Machine.trap):
        if pc == upd:
            frame_marks.append(outer.cyc)
            # emulate the original instruction (ldy #imm)
            if orig == 0xA0:                 # ldy #imm
                outer.y = outer.m[upd + 1]
                outer.zv = outer.nv = outer.y
                c = 2
            else:                            # lda (zp),y (mfetch0 since the skill pass)
                assert orig == 0xB1
                zp = outer.m[upd + 1]
                base = outer.m[zp] | (outer.m[(zp + 1) & 0xFF] << 8)
                ea = (base + outer.y) & 0xFFFF
                outer.a = outer.m[ea] if outer.rp[ea >> 8] else outer.rd(ea)
                outer.zv = outer.nv = outer.a
                c = 5 + ((base & 0xFF) + outer.y > 0xFF)
            outer.pc = upd + 2
            outer.cyc += c
            if outer.prof:
                outer.pcyc[upd] += c
            # the generated run loop's `finally` writes its locals back over these:
            # keep them and re-apply after the Mark is caught
            outer._post = (outer.a, outer.y, outer.zv, outer.nv, outer.pc, outer.cyc)
            raise Mark()
        return _orig(outer, pc)
    mc.trap = trap
    video = []
    busy = []
    waits = []
    blits = []
    total = warm + frames
    last_c = None
    lim = max_cycles or (total + 200) * FRAME * 12
    spin_pcs = {}
    for t, (br, body, cost) in mc.spin_tab.items():
        for j in body:
            spin_pcs[j.addr] = t
    # wait_vblank's `?w cmp RTCLOK3 / beq ?w` idles until the VBLANK tick (the page flip
    # sync): pacing, not work -- a faster frame only waits longer there
    wv = L.get('wait_vblank')
    vbl_wait = ()
    if wv is not None and mc.m[wv + 2] == 0xC5 and mc.m[wv + 4] == 0xF0:
        vbl_wait = (wv + 2, wv + 4)
    pace_heads = set()
    wait_heads = set()
    for t, (br, body, cost) in mc.spin_tab.items():
        rd = [j.arg for j in body if j.mode in ('zp', 'abs')]
        if any(RTCLOK <= r <= RTCLOK + 2 for r in rd):
            pace_heads.add(t)
        elif any(r == 0xD653 for r in rd):
            wait_heads.add(t)
    prev_pcyc = None
    while len(frame_marks) < total + 1 and mc.cyc < lim:
        try:
            mc.run_until(lim)
        except Mark:
            (mc.a, mc.y, mc.zv, mc.nv, mc.pc, mc.cyc) = mc._post
            n = len(frame_marks)
            if n == warm + 1 and prof:
                mc.prof = True
                mc.pcyc = [0] * 65536
                mc.pcnt = [0] * 65536
                prev_pcyc = None
            if n > warm + 1 or (n == warm + 1 and False):
                pass
            # displayed page content + palette = the video-out hash of THIS frame
            hires = mc.m[L['hires']] if 'hires' in L else 0
            page = mc.flips[-1][1] if mc.flips else 0
            size = 64000 if hires else 32000
            hsh = hashlib.sha1(bytes(mc.vram[page * 65536:page * 65536 + size]) + bytes(mc.pal[1])).hexdigest()
            video.append(hsh)
            if mc.prof:
                cur = mc.pcyc
                snap = (sum(cur), sum(cur[a] for t in pace_heads for a in _body_addrs(mc, t))
                        + sum(cur[a] for a in vbl_wait),
                        sum(cur[a] for t in wait_heads for a in _body_addrs(mc, t)), mc.blit_cyc_total, mc.nblits)
                if prev_pcyc is not None:
                    busy.append(snap[0] - prev_pcyc[0] - (snap[1] - prev_pcyc[1]))
                    waits.append(snap[2] - prev_pcyc[2])
                    blits.append((snap[3] - prev_pcyc[3], snap[4] - prev_pcyc[4]))
                prev_pcyc = snap
            continue
    dt = time.time() - t0
    res = dict(machine=mc, video=video, busy=busy, waits=waits, blits=blits, frames=len(frame_marks),
               secs=dt, pace_heads=pace_heads, wait_heads=wait_heads)
    return res


def _body_addrs(mc, t):
    return [j.addr for j in mc.spin_tab[t][1]]


def proc_of(ins):
    """addr -> enclosing label (nearest non-? label at or before it)."""
    names = []
    for i in ins:
        for nm in i.labels:
            if not nm.startswith('?'):
                names.append((i.addr, nm))
    names.sort()
    import bisect
    keys = [a for a, _ in names]

    def f(a):
        k = bisect.bisect_right(keys, a) - 1
        return names[k][1] if k >= 0 else '?'
    return f


def report(res, tag, top=40, lines=0, dump=None):
    mc = res['machine']
    out = []
    say = out.append
    n = len(res['busy'])
    say('bench %s: %d frames measured (%d displayed incl. warm-up), %.1f s host time'
        % (tag, n, res['frames'], res['secs']))
    if n:
        b = sum(res['busy']) / n
        w = sum(res['waits']) / n
        bc = sum(x[0] for x in res['blits']) / n
        nb = sum(x[1] for x in res['blits']) / n
        say('  busy cycles / frame      %10.0f  (everything but the pacing spin)' % b)
        say('  blitter-wait cycles/frame%10.0f  (CPU spinning on BL_BUSY, inside busy)' % w)
        say('  CPU work / frame         %10.0f  (busy - blitter waits)' % (b - w))
        say('  blits / frame %.1f, blitter cycles / frame %.0f (= %.0f CPU cycles of blitter time)'
            % (nb, bc, bc / BLIT_RATE))
        say('  -> render-bound fps at 1.77 MHz PAL: %.2f' % (1773447.0 / b if b else 0))
    vs = hashlib.sha1(''.join(res['video']).encode()).hexdigest()
    say('VIDEOSHA %s  (%d frames)' % (vs, len(res['video'])))
    vm = bytes(mc.m[0xB000:0xB3C0])
    say('VMSHA    %s' % hashlib.sha1(vm).hexdigest())
    if mc.prof:
        f = proc_of(mc.ins)
        per = {}
        for a in range(65536):
            c = mc.pcyc[a]
            if c:
                p = f(a)
                per[p] = per.get(p, 0) + c
        tot = sum(per.values()) or 1
        say('')
        say('cycles per proc (self), per frame, measured window:')
        for p, c in sorted(per.items(), key=lambda x: -x[1])[:top]:
            say('  %-28s %10.0f  %5.1f%%' % (p, c / max(1, n), 100.0 * c / tot))
        if dump:
            import json
            json.dump({'%d' % a: mc.pcyc[a] / max(1, n) for a in range(65536) if mc.pcyc[a]},
                      open(dump, 'w'))
            json.dump({'%d' % a: mc.pcnt[a] / max(1, n) for a in range(65536) if mc.pcnt[a]},
                      open(dump.replace('.json', '_cnt.json'), 'w'))
        if lines:
            at = {i.addr: i for i in mc.ins if not i.data and i.mn}
            say('')
            say('hot instructions, cycles per frame:')
            for a in sorted(range(65536), key=lambda x: -mc.pcyc[x])[:lines]:
                i = at.get(a)
                where = '%s:%d' % (os.path.basename(i.file), i.line) if i else '?'
                say('  $%04X %8.0f  %-22s %s' % (a, mc.pcyc[a] / max(1, n), where,
                                               i.src.strip()[:60] if i else ''))
    txt = '\n'.join(out)
    print(txt)
    d = os.path.join(PROJ, 'out', 'bench')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'bench_%s.txt' % tag), 'w') as fh:
        fh.write(txt + '\n')
    return vs


def main():
    a = sys.argv[1:]

    def opt(name, default, conv=int):
        if name in a:
            i = a.index(name)
            return conv(a[i + 1])
        return default
    frames = opt('--frames', 20)
    warm = opt('--warm', 10)
    part = opt('--part', None)
    pos = opt('--pos', None)
    top = opt('--top', 40)
    bdir = opt('--build', None, str)
    tag = opt('--tag', 'cur' if not bdir else os.path.basename(bdir.rstrip('/\\')), str)
    res = frame_bench(bdir, frames, warm, part, pos)
    if part is not None:
        tag += '_p%d' % part
    report(res, tag, top, opt('--lines', 0), opt('--dump', None, str))


if __name__ == '__main__':
    main()
