# build_awgame.ps1 - build the bootable GAME disk (awgame.xex + awgame.atr).
#
# Two-pass: the on-disk part-sector table (game_atr.inc) depends on the xex size,
# and the xex embeds that table. The table's byte-size is invariant, so the xex
# size is stable and the layout converges in two passes.
#
#   pass 1: build xex (old table) -> make_atr regenerates the table + a draft atr
#   pass 2: rebuild xex (correct table) -> make_atr writes the final bootable atr
#
# Run from the project root:   .\build_awgame.ps1

$ErrorActionPreference = "Stop"
$mads = ".\mads.exe"

# boot loader (3 sectors) -- rebuild only if the source changed
if (-not (Test-Path "out\boot.bin") -or
    ((Get-Item "src_game\bootloader.asm").LastWriteTime -gt (Get-Item "out\boot.bin").LastWriteTime)) {
    Write-Host "[boot] assembling bootloader..."
    & $mads "src_game\bootloader.asm" "-o:out\boot.xex" | Out-Null
    $b = [System.IO.File]::ReadAllBytes("out\boot.xex")
    [System.IO.File]::WriteAllBytes("out\boot.bin", $b[6..($b.Length-1)])
}

Write-Host "[pass 1] build xex"
& $mads "src_game\awgame.asm" "-o:awgame.xex" | Select-Object -Last 1
Write-Host "[pass 1] make atr (regenerate sector table)"
python "tools\make_game_atr.py" | Select-Object -Last 2

Write-Host "[pass 2] rebuild xex with the correct table"
& $mads "src_game\awgame.asm" "-o:awgame.xex" | Select-Object -Last 1
Write-Host "[pass 2] write final bootable atr"
python "tools\make_game_atr.py" | Select-Object -Last 2

Write-Host "[guard] layout check"
python "tools\check_layout.py" | Select-Object -Last 3
Write-Host "[guard] xex boundary check (segments vs reserved RAM / windows)"
python "tools\check_xex.py" "awgame.xex"
if ($LASTEXITCODE -ne 0) { throw "check_xex.py FAILED - an XEX segment lands in reserved RAM (window/ROM/VM)" }

Write-Host ""
Write-Host "Done. Boot awgame.atr on D1: on an Atari XE/XL (it loads awgame.xex, then streams parts)."
