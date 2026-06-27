# build_intro.ps1 - build the INTRO (awintro.xex) with SFX.
#
# The intro now ships sound effects, so two pre-build PC steps must run first
# (they regenerate files that aw_data.asm `ins`-includes into the xex):
#   1. gen_intro_sfx.py  -> out/intro_sfx.bin + src/aw_sfx_tables.inc + map
#   2. aw_playlist.py     -> out/intro_playlist.bin (now carries 0x08 SOUND ops)
# then assemble awvbxe.asm.
#
# Run from the project root:   .\build_intro.ps1

$ErrorActionPreference = "Stop"

Write-Host "[1/3] pack SFX (4-bit -> VRAM blob + address tables)"
python "tools\gen_intro_sfx.py"

Write-Host "[2/3] flatten playlist (visual stream + SOUND events)"
python "tools\aw_playlist.py" | Select-Object -Last 3

Write-Host "[3/3] assemble awintro.xex"
& ".\mads.exe" "src\awvbxe.asm" "-o:awintro.xex" | Select-Object -Last 1

Write-Host ""
Write-Host "Done. Run awintro.xex in Altirra (VBXE required) to hear the SFX."
