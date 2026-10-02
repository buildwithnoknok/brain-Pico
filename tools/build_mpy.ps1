# SPDX-License-Identifier: MIT
# build_mpy.ps1 - precompile the brain's libraries to .mpy (CircuitPython bytecode).
#
# Why: the Pico compiles every imported .py in RAM at boot. noknok.py is ~190 KB
# of source; its parse tree needs large contiguous heap blocks, and on 2 Oct 2026
# it no longer fit (MemoryError at "import noknok", brain never reached WiFi).
# A .mpy is already compiled: it loads with a fraction of the RAM and faster.
# First piece of DEV-38 (one-file UF2 with precompiled/frozen modules).
#
# Rules:
#   - mpy-cross MUST match the CircuitPython version on the Pico (10.3.1 = mpy v6.3).
#     Official builds: https://adafruit-circuit-python.s3.amazonaws.com/index.html?prefix=bin/mpy-cross/
#   - code.py and boot.py stay .py (CircuitPython only runs those by name).
#   - On the Pico a .py beats a .mpy of the same name - delete/rename the .py there.
#   - The repo keeps only the .py sources; .mpy files are build output (pico-deploy).
#
# Usage (PowerShell, from anywhere):
#   .\build_mpy.ps1
#   .\build_mpy.ps1 -MpyCross C:\path\mpy-cross.exe -OutDir C:\somewhere

param(
    [string]$MpyCross = "C:\Users\chris\noknok\tools\mpy-cross\mpy-cross-10.3.1.exe",
    [string]$OutDir   = "C:\Users\chris\noknok\pico-deploy\mpy"
)

$ErrorActionPreference = "Stop"
$src = Join-Path $PSScriptRoot "..\software"

# The libraries code.py and products import. Add new library files here.
$libs = @("noknok.py", "noknok_rpc.py", "noknok_usb.py", "module_flasher.py")

if (-not (Test-Path $MpyCross)) { throw "mpy-cross not found: $MpyCross" }
Write-Output ("Compiler: " + (& $MpyCross --version))
New-Item -ItemType Directory -Force $OutDir | Out-Null

foreach ($lib in $libs) {
    $in  = Join-Path $src $lib
    $out = Join-Path $OutDir ([IO.Path]::ChangeExtension($lib, ".mpy"))
    # -O1 strips assert statements only; docstrings are never kept in .mpy anyway.
    & $MpyCross -O1 -o $out $in
    if ($LASTEXITCODE -ne 0) { throw "mpy-cross failed on $lib" }
    $kbIn  = [math]::Round((Get-Item $in).Length / 1KB)
    $kbOut = [math]::Round((Get-Item $out).Length / 1KB)
    Write-Output ("{0,-20} {1,4} KB source -> {2,4} KB .mpy" -f $lib, $kbIn, $kbOut)
}
Write-Output "Done: $OutDir"
