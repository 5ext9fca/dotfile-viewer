[CmdletBinding()]
param([switch]$Strict)
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Python = if ($env:DOTFILES_PYTHON) { $env:DOTFILES_PYTHON } else { Join-Path $Root '.venv/Scripts/python.exe' }
& $Python -c 'import sys; assert sys.version_info[:2] == (3, 14), "Validation requires Python 3.14"'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Push-Location $Root
try { & $Python -m unittest discover -s tests/viewer -v; exit $LASTEXITCODE } finally { Pop-Location }
