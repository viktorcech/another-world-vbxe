# build.ps1 - THE build. Produces ONE bootable disk: awgame_full.atr
#
#   intro (with music + SFX)  ->  ESC or its natural end chains into  ->  the full GAME
#
# This replaces the old build_intro.ps1 / build_awgame.ps1 / build_full.ps1 trio.
# Keeping three scripts was actively dangerous: build_awgame.ps1 regenerated
# src_game/game_atr.inc for a STANDALONE disk (game xex at sector 4) while
# build_full.ps1 regenerates it for the combined disk (game xex after the intro).
# Whichever ran last silently decided which sector table got baked into awgame.xex,
# so a stale run left the game reading the WRONG sectors off the combined disk.
# One script = one layout = one truth.
#
# Disk layout (128-byte sectors):
#   1-3        boot loader (src_game/bootloader.asm)
#   4..        awintro.xex   (intro; the boot loader runs it first)
#   GAME_SEC.. awgame.xex     (loaded when the intro ends or ESC is pressed ->
#                             intro_done re-enters the boot loader at cur_sec=GAME_SEC,
#                             see src/aw_exit.asm)
#   base..     game part blob (out/game_parts.bin, addressed by src_game/game_atr.inc)
#
# Two build-time constants depend on the intro's size:
#   GAME_SEC        = 4 + intro_sectors              -> baked into awintro.xex (mads -d:)
#   part-table base = GAME_SEC + game_xex_sectors    -> baked into game_atr.inc
# so the intro is assembled twice (pass 1 measures, pass 2 bakes GAME_SEC) and the
# game keeps its own 2-pass table convergence, rebased by --xex-start.
#
# Run from the project root:   .\build.ps1
#
# Prereq: out/intro_music.bin (tools/render_intro_audio.py). That step takes minutes
# and the music source never changes, so it is NOT re-run here -- only checked for.
#
# NOTE: the 2026-07-01 hero walk-speed x2 bytecode patch was REMOVED on 2026-07-02
# (user: felt like "floating"/skating steps). tools/patch_hero_speed.py still exists
# standalone if it is ever wanted again.

# -ForceCovox builds a TEST disk: both sound players skip the covox probe and go
# 8-bit unconditionally, so POKEY is never written. Remove the Covox device in
# Altirra and the disk must be SILENT -- that is the check that the sound you
# hear with it attached is really coming out of the covox and not out of POKEY.
# NEVER ship a disk built this way: on a machine without a covox it has no audio.
param([switch]$ForceCovox)

$ErrorActionPreference = "Stop"
$mads = ".\mads.exe"
$covoxDef = @()
if ($ForceCovox) {
    Write-Host "*** -ForceCovox: TEST build, POKEY output disabled in both players ***"
    $covoxDef = @("-d:COVOX_FORCE=1")
}

function Sectors([string]$file) { [math]::Ceiling((Get-Item $file).Length / 128) }

if (-not (Test-Path "out\intro_music.bin")) {
    throw "out\intro_music.bin missing - run: python tools\render_intro_audio.py (slow, one-off)"
}

# --- boot loader (3 sectors) : ALWAYS re-assembled --------------------------------
# This used to be skipped when bootloader.asm was not NEWER than out/boot.bin, and
# that mtime guard shipped a stale loader for weeks: the source was edited to put
# cur_sec/buf_pos right behind the boot header (init moved $0706 -> $070D) but kept
# an older timestamp, so the June binary (code starting at $0706) stayed on every
# disk. intro_done then wrote cur_sec/buf_pos ON TOP OF the loader's first
# instructions and jumped into the wreckage -> the game never came up after ESC.
# Assembling 155 lines costs milliseconds; never trade that for a staleness bug.
Write-Host "[boot] assembling bootloader..."
& $mads "src_game\bootloader.asm" "-o:out\boot.xex" | Out-Null
$b = [System.IO.File]::ReadAllBytes("out\boot.xex")
[System.IO.File]::WriteAllBytes("out\boot.bin", $b[6..($b.Length-1)])

# --- intro data (SFX tables + playlist) ------------------------------------------
# Both regenerate from the ORIGINAL PC game data in orig/. When that folder is not
# present, reuse the artifacts already in out/ -- but say so, and refuse to build a
# disk if they are missing too. (These steps used to fail silently: python's exit
# code is invisible to $ErrorActionPreference, so a broken regeneration just
# scrolled past and the disk shipped whatever was lying in out/.)
if (Test-Path "orig\MEMLIST.BIN") {
    Write-Host "[intro 1/5] pack SFX (VRAM blob + address tables)"
    python "tools\gen_intro_sfx.py" | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) { throw "gen_intro_sfx.py FAILED" }
    Write-Host "[intro 2/5] flatten playlist (visual stream + SOUND/MUSIC events)"
    python "tools\aw_playlist.py" | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) { throw "aw_playlist.py FAILED" }
} else {
    Write-Host "[intro 1-2/5] orig/ missing -> REUSING out\intro_playlist.bin + SFX tables"
    foreach ($f in @("out\intro_playlist.bin", "out\intro_poly.bin", "out\intro_sfx.bin",
                     "src\aw_sfx_tables.inc")) {
        if (-not (Test-Path $f)) { throw "$f missing and orig/ is gone -- restore orig/ to rebuild it" }
    }
}

# --- intro pass 1 : measure its size to derive GAME_SEC ---------------------------
Write-Host "[intro 3/5] assemble awintro.xex (pass 1, GAME_SEC placeholder)"
& $mads "src\awvbxe.asm" "-d:GAME_SEC=0" @covoxDef "-o:awintro.xex" | Select-Object -Last 1
$introSectors = Sectors "awintro.xex"
$gameSec = 4 + $introSectors
Write-Host "        intro = $introSectors sectors  ->  GAME_SEC = $gameSec"

# --- intro pass 2 : bake the real GAME_SEC (same byte size -> layout is stable) ---
Write-Host "[intro 4/5] assemble awintro.xex (pass 2, GAME_SEC=$gameSec)"
& $mads "src\awvbxe.asm" "-d:GAME_SEC=$gameSec" @covoxDef "-o:awintro.xex" "-l:out\awintro.lst" | Select-Object -Last 1
if ((Sectors "awintro.xex") -ne $introSectors) {
    throw "intro size changed between passes ($introSectors -> $(Sectors 'awintro.xex') sectors) -- GAME_SEC would be wrong"
}
# The COVOX mode rewrites live IRQ code at run time (snd_go_covox), so mads can
# only see one of the two versions. This replays the patch on the binary and
# disassembles the result -- a slot whose covox image is the wrong length would
# otherwise break ONLY on the machines that actually have a covox.
Write-Host "[intro 5/5] covox SMC check"
python "tools\verify_covox.py" "awintro.xex" "out\awintro.lst" | Select-Object -Last 1
if ($LASTEXITCODE -ne 0) { throw "verify_covox.py FAILED - the covox code patch is not slot-clean" }
# ... and the probe that decides whether those images are installed at all. It
# runs the assembled snd_detect on a 6502 against five machines, because the
# one thing the build machine cannot do is answer the probe.
python "tools\verify_covox_detect.py" "awintro.xex" "out\awintro.lst" | Select-Object -Last 1
if ($LASTEXITCODE -ne 0) { throw "verify_covox_detect.py FAILED - snd_detect answers wrong on at least one machine" }
Write-Host "        awintro.xex done ($introSectors sectors)"

# --- game : 2-pass xex + part table, rebased so the blob sits AFTER the intro -----
Write-Host "[game 1/4] build xex (pass 1)"
& $mads "src_game\awgame.asm" @covoxDef "-o:awgame.xex" "-l:out\awgame.lst" | Select-Object -Last 1
Write-Host "[game 2/4] regenerate part table (base after intro; --xex-start $gameSec)"
python "tools\make_game_atr.py" "--xex-start" "$gameSec" "--no-atr" | Select-Object -Last 1
Write-Host "[game 3/4] rebuild xex with the correct table (pass 2)"
& $mads "src_game\awgame.asm" @covoxDef "-o:awgame.xex" "-l:out\awgame.lst" | Select-Object -Last 1
Write-Host "[game 4/4] finalize part table"
python "tools\make_game_atr.py" "--xex-start" "$gameSec" "--no-atr" | Select-Object -Last 1

Write-Host "[guard] covox SMC check (game player)"
python "tools\verify_covox.py" "awgame.xex" "out\awgame.lst" | Select-Object -Last 1
if ($LASTEXITCODE -ne 0) { throw "verify_covox.py FAILED on the game - the covox code patch is not slot-clean" }
Write-Host "[guard] covox probe check (game player)"
python "tools\verify_covox_detect.py" "awgame.xex" "out\awgame.lst" | Select-Object -Last 1
if ($LASTEXITCODE -ne 0) { throw "verify_covox_detect.py FAILED on the game - snd_detect answers wrong on at least one machine" }

# --- guards -----------------------------------------------------------------------
# check_layout re-assembles awgame.xex itself; the includes are final by now, so the
# rebuild is byte-identical. It must run AFTER the table is finalized, never before.
Write-Host "[guard] layout check (code under `$4000, VRAM banks, RAM blocks)"
python "tools\check_layout.py" | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) { throw "check_layout.py FAILED - something overflows its limit" }
Write-Host "[guard] xex boundary check (segments vs reserved RAM / windows)"
python "tools\check_xex.py" "awgame.xex"
if ($LASTEXITCODE -ne 0) { throw "check_xex.py FAILED - an XEX segment lands in reserved RAM" }

# --- assemble the one bootable disk -----------------------------------------------
Write-Host "[disk] assemble awgame_full.atr"
python "tools\make_full_atr.py"

Write-Host ""
Write-Host "Done. Boot awgame_full.atr on D1: in Altirra (VBXE required)."
Write-Host "  -> intro with music; ESC (or its end) chains into the game."
