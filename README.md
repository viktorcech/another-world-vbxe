# Another World VBXE

A port of **Another World** (a.k.a. *Out of This World*) to the **Atari XL/XE** with **VBXE**, written in 6502 assembly with a Python asset pipeline.

The repo ships the **engine source and build tooling** — it does **not** include the original game data (see below).

## Hardware

Runs on **real Atari hardware**.

- **Atari XE/XL** with **64 KB RAM** and **VBXE**.
- **Rapidus** accelerator **recommended** (for full speed).
- **Covox** 8-bit DAC — **optional**, auto-detected at start. Where one is found (PokeyMAX or a compatible card at `$D280`), both sample players re-route their output to it instead of POKEY's volume-only channels: same 4-bit data, but a linear R-2R ladder instead of POKEY's bent DAC, and no channel-summing compression. Nothing to configure; without one the POKEY code is what runs.

## Build requirements

- **[Mad Assembler (MADS)](https://github.com/tebe6502/Mad-Assembler)** — put `mads.exe` in the project root (the build script calls `.\mads.exe`).
- **Python 3** — for the asset/data pipeline in `tools/`.

## Original game data (not included)

The game build packs assets from the **original DOS release of Another World**. Its intellectual property is owned by **Éric Chahi** — the game was originally published by Delphine Software in 1991, and Chahi acquired the rights after Delphine closed in 2004. Those original assets are **not distributed here**; to build the full game disk you must supply your own legally-owned copy.

Create an `orig/` folder in the project root containing the original DOS files:

```
orig/
├── MEMLIST.BIN
└── BANK01 … BANK0D
```

`tools/aw_pack.py` reads these to produce the Atari assets.

## Build

One slow, one-off step first — the pre-rendered music bed. It takes minutes, the music source never changes, and `build.ps1` refuses to run without it:

```powershell
python tools\render_intro_audio.py    # -> out\intro_music.bin
```

Then, from the project root:

```powershell
.\build.ps1
```

That produces **one bootable disk, `awgame_full.atr`**: the intro plays first (music + SFX) and chains straight into the full game when it ends or on **ESC**. Boot it from **D1:** on an Atari XE/XL.

The single script is deliberate. The intro's size decides where the game xex and the part blob land on the disk, and those offsets are baked into both binaries — so SFX packing, the playlist, both assembler passes, the sector tables and the build guards all have to come from one run to stay consistent.

`build.ps1 -ForceCovox` builds a **test** disk with the covox probe skipped and POKEY output disabled, to verify that the audio you hear really comes out of the covox. Never ship a disk built that way — it is silent on a machine without one.

## Layout

| Path | Contents |
|------|----------|
| `src/` | Intro engine (VBXE renderer, replayer, SFX) |
| `src_game/` | Game build — bytecode VM, disk I/O, cell cache, bootloader |
| `tools/` | Python pipeline (asset packing, ATR builder, simulators, profilers, build guards) |
| `build.ps1` | The build |

## Credits

- Original game: **Another World** — created by Éric Chahi, originally published by Delphine Software (1991); IP owned by Éric Chahi.
- Some files under `tools/` (`_rawgl_*`, `_staticres.cpp`) derive from the open-source **[rawgl](https://github.com/cyxx/rawgl)** reimplementation by Gregory Montoir, used to interpret the original bytecode/resources.
- Atari XL/XE + VBXE port: **w1k**.
