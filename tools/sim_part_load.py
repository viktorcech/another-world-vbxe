#!/usr/bin/env python3
"""sim_part_load.py - run a WHOLE part stream through the real assembled loader.

sim_hs_sio.py only ever timed ONE sector, which is exactly the wrong granularity:
the interesting question is what 450 consecutive sectors cost, i.e. whether a
high-speed failure is paid ONCE (read_one latches hs_div=1) or on EVERY sector.
This drives the real stream_to_vram/read_sectors over a modelled SIO2SD and
prints where the wall clock actually goes.

    python tools/sim_part_load.py              # every scenario, 450-sector stream
    python tools/sim_part_load.py -n 40        # shorter stream (faster)
"""
import os, sys, struct, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location('hs', os.path.join(HERE, 'sim_hs_sio.py'))
hs = importlib.util.module_from_spec(spec); spec.loader.exec_module(hs)

MHZ = hs.MHZ
XEX = os.path.join(PROJ, 'out', 'awgame_test.xex')
LAB = os.path.join(PROJ, 'out', 'awgame_test.lab')
ATR = os.path.join(PROJ, 'awgame.atr')

DK_SEC, DK_CNT, DK_BANK = 0xB3C8, 0xB3CA, 0xB3CC
HS_DIV = 0xB3D6


class Counting(hs.Machine):
    """Machine + a tally of which read path each sector actually took."""

    def __init__(self, *a, hs_entry=0, **kw):
        super().__init__(*a, **kw)
        self.hs_entry = hs_entry
        self.hs_tries = 0
        self.siov_calls = 0
        self.marks = []                       # (cycle, 'HS'|'SIO')

    def siov(self):
        t0 = self.cyc
        super().siov()
        self.marks.append((t0, self.cyc - t0, 'SIO'))

    def call(self, addr, budget=40_000_000):
        c = self.cpu
        c.pc = addr; c.sp = 0xFD
        c.push(0x99); c.push(0x98)
        while c.pc != 0x9999:
            if c.pc == 0xE459:
                self.siov()
                lo = c.pop(); hi = c.pop(); c.pc = ((hi << 8) | lo) + 1
                continue
            if c.pc == self.hs_entry:
                self.hs_tries += 1
                self._hs_t0 = self.cyc
            self.cyc += c.step()
            if self.cyc > budget:
                return False
        return True


def atr_reader(path):
    d = open(path, 'rb').read()
    assert struct.unpack('<H', d[:2])[0] == 0x0296
    body = d[16:]

    def read(sec):
        o = (sec - 1) * 128
        return body[o:o + 128].ljust(128, b'\0')
    return read


def scenario(name, mem, L, disk, nsec, start_sec, poll=True, **drive_kw):
    d = hs.Drive(disk, **drive_kw)
    m = Counting(mem, d, hs_entry=L['HS_READ_SECTOR'])
    if poll:                                          # the OLD load_part: ask $3F
        m.call(L['HS_POLL'], 4_000_000_000)
    else:                                             # the NEW load_part: never ask
        m.m[HS_DIV] = 0x01
    div = m.m[HS_DIV]
    m.m[DK_SEC] = start_sec & 0xFF; m.m[DK_SEC + 1] = start_sec >> 8
    m.m[DK_CNT] = nsec & 0xFF; m.m[DK_CNT + 1] = nsec >> 8
    m.cpu.a = 0x14                                    # POLY_BANK0
    t0 = m.cyc
    ok = m.call(L['STREAM_TO_VRAM'], 4_000_000_000)
    dt = (m.cyc - t0) / MHZ / 1e6
    print(f'  {name}')
    print(f'      hs_div po $3F      : ${div:02X}'
          f"{'  (odmietnute -> stock SIOV)' if div in (0, 1) else ''}")
    print(f'      HS pokusov         : {m.hs_tries} na {nsec} sektorov')
    print(f'      SIOV volani        : {len(m.marks)}')
    print(f'      cas na {nsec} sektorov: {dt:.1f} s   -> {nsec/dt:.1f} sektorov/s'
          f'   (cely part 1511 sekt. = {1511*dt/nsec:.0f} s)')
    if not ok:
        print('      !!! nedobehlo v limite')
    return m


def main():
    n = 450
    if '-n' in sys.argv:
        n = int(sys.argv[sys.argv.index('-n') + 1])
    if not os.path.exists(XEX):
        sys.exit('chyba out/awgame_test.xex - mads.exe src_game/awgame.asm '
                 '-o:out/awgame_test.xex -t:out/awgame_test.lab')
    mem = hs.load_xex(XEX)
    L = hs.labels(LAB)
    disk = atr_reader(ATR)
    start = 175                                       # first part sector on awgame.atr

    print(f'=== stream {n} sektorov od {start} (awgame.atr, part blob) ===')
    scenario('D. AKTUALNY load_part: ziadny $3F, vsetko stock SIOV (ako boot loader)',
             mem, L, disk, n, start, poll=False, cmd_setup_us=400, hs_index=0x0A)
    scenario('A. tolerantne zariadenie (nepotrebuje ziadny COMMAND setup) -> HS ide',
             mem, L, disk, n, start, cmd_setup_us=0, hs_index=0x0A)
    scenario('B. SIO2SD odpoveda $3F=$0A ale NEPOCUJE HS ramec (chyba drzania COMMAND)',
             mem, L, disk, n, start, cmd_setup_us=400, hs_index=0x0A)
    scenario('C. SIO2SD na $3F neodpoveda vobec -> vsetko cez stock SIOV',
             mem, L, disk, n, start, cmd_setup_us=400, hs_index=None)


if __name__ == '__main__':
    main()
