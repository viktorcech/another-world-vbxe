# Another World VBXE

A port of **Another World** (a.k.a. *Out of This World*) to the **Atari XL/XE** with **VBXE**, written in 6502 assembly with a Python asset pipeline.

The repo ships the **engine source and build tooling** — it does **not** include the original game data (see below).

## Hardware

Runs on **real Atari hardware** — no emulator required.

- **Atari XE/XL** with **64 KB RAM** and **VBXE**.
- **Rapidus** accelerator **recommended** (for full speed).
- [Altirra](https://www.virtualdub.org/altirra.html) (with VBXE) is optional, for development/testing.

## Build requirements

- **[Mad Assembler (MADS)](https://github.com/tebe6502/Mad-Assembler)** — put `mads.exe` in the project root (the build scripts call `.\mads.exe`).
- **Python 3** — for the asset/data pipeline in `tools/`.

## Original game data (not included)

The game build packs assets from the **original DOS release** of Another World, which is **© Delphine Software** and is **not distributed here**. To build the full game disk you must supply your own legally-owned copy.

Create an `orig/` folder in the project root containing the original DOS files:

```
orig/
├── MEMLIST.BIN
└── BANK01 … BANK0D
```

`tools/aw_pack.py` reads these to produce the Atari assets.

## Build

Run from the project root.

```powershell
# Intro (awintro.xex) — VBXE animation + SFX
.\build_intro.ps1

# Bootable game disk (awgame.xex + awgame.atr)
.\build_awgame.ps1
```

Then boot `awgame.atr` from **D1:** on an Atari XE/XL (VBXE; Rapidus recommended), or run `awintro.xex` directly. Altirra with VBXE also works for testing.

## Layout

| Path | Contents |
|------|----------|
| `src/` | Intro engine (VBXE renderer, replayer, SFX) |
| `src_game/` | Game build — bytecode VM, disk I/O, cell cache, bootloader |
| `tools/` | Python pipeline (asset packing, ATR builder, simulators, profilers) |
| `build_*.ps1` | Build scripts |

## Credits

- Original game: **Another World** by Éric Chahi / Delphine Software (1991).
- Some files under `tools/` (`_rawgl_*`, `_staticres.cpp`) derive from the open-source **[rawgl](https://github.com/cyxx/rawgl)** reimplementation by Gregory Montoir, used to interpret the original bytecode/resources.
- Atari XL/XE + VBXE port: **w1k**.
