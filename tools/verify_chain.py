#!/usr/bin/env python3
"""
verify_chain.py - simulate the boot loader's intro->game chain on the full disk.

Replays src_game/bootloader.asm's get_byte/parse_seg state machine over
awgame_full.atr starting at cur_sec=GAME_SEC (what intro_done jumps into) and
checks: the XEX header, every segment (INIT/RUN handling included), and that
the byte stream equals awgame.xex. Localises a broken ESC-skip chain to either
the disk layout (mismatch here) or the runtime state (parse is clean).

    python tools/verify_chain.py [game_sec]     # default 2869
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
SECTOR = 128


def main():
    game_sec = int(sys.argv[1]) if len(sys.argv) > 1 else 2869
    atr = open(os.path.join(PROJ, 'awgame_full.atr'), 'rb').read()
    xex = open(os.path.join(PROJ, 'awgame.xex'), 'rb').read()
    hdr = atr[:16]
    assert hdr[0] == 0x96 and hdr[1] == 0x02, 'not an ATR'
    data = atr[16:]
    nsec = len(data) // SECTOR
    print(f'ATR: {nsec} sectors   game_sec={game_sec}')

    # loader model: 1-based sectors
    state = {'cur': game_sec, 'pos': SECTOR}
    def get_byte():
        if state['pos'] >= SECTOR:
            sec = state['cur']
            state['buf'] = data[(sec - 1) * SECTOR: sec * SECTOR]
            state['cur'] += 1
            state['pos'] = 0
        b = state['buf'][state['pos']]
        state['pos'] += 1
        return b

    # stream-vs-xex comparison
    stream_off = [0]
    def gb_checked():
        b = get_byte()
        i = stream_off[0]
        if i < len(xex) and xex[i] != b:
            print(f'  !! stream byte {i} = ${b:02X} but awgame.xex has ${xex[i]:02X}')
            sys.exit(1)
        stream_off[0] += 1
        return b

    b0, b1 = gb_checked(), gb_checked()
    print(f'header: ${b0:02X} ${b1:02X}  {"ok" if (b0, b1) == (0xFF, 0xFF) else "BAD (expected FF FF)"}')

    nseg = 0
    while True:
        lo = gb_checked() | (gb_checked() << 8)
        if lo == 0xFFFF:
            continue
        hi = gb_checked() | (gb_checked() << 8)
        if lo == 0x02E2:
            tgt = gb_checked() | (gb_checked() << 8)
            print(f'  INIT -> ${tgt:04X}')
            nseg += 1
            continue
        if lo == 0x02E0:
            tgt = gb_checked() | (gb_checked() << 8)
            print(f'  RUN  -> ${tgt:04X}')
            nseg += 1
            break
        n = hi - lo + 1
        for _ in range(n):
            gb_checked()
        print(f'  data ${lo:04X}-${hi:04X}  ({n} B)')
        nseg += 1

    print(f'{nseg} segments parsed, {stream_off[0]} bytes streamed '
          f'(awgame.xex = {len(xex)} B), ended in sector {state["cur"] - 1}')
    if stream_off[0] != len(xex):
        print(f'  NOTE: stream ended {"before" if stream_off[0] < len(xex) else "after"} '
              f'the xex length -- trailing bytes unparsed')
    print('chain byte stream: OK (identical to awgame.xex up to the RUN vector)')


if __name__ == '__main__':
    main()
