# build-exe.ps1 -- build the distributable Lutron Builder.exe.
#
#   powershell -ExecutionPolicy Bypass -File .\build-exe.ps1
#
# Run it from the repository root on Windows. Needs Python 3.10+ on PATH (or
# pass -Python). The result lands in .\dist\Lutron Builder.exe.
#
# Build on the same architecture your users run: -Arch x64 is normal Intel/AMD
# Windows and is what you should ship. An ARM64 Windows machine will silently
# produce an ARM64 exe that most people cannot run -- check with
# `file "dist\Lutron Builder.exe"` (or Get-FileHash won't tell you; PyInstaller
# does not warn).
param(
  [string]$Python = "python",
  [string]$Arch = "x64"
)
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$work = Join-Path $env:LOCALAPPDATA "LutronBuilderBuild"

# EVERY runtime dependency belongs on this line: whatever is missing here is
# missing from the exe, and the person who finds out is a stranger being told
# to "pip install" something on a machine with no Python. pypdf was absent
# from v1.0.0 through v1.3.2 -- proven by unpacking the shipped exe on
# 2026-08-13 -- which silently broke reading a plan's text layer and reading a
# specification PDF. Adding an import to the app means adding it here.
#
# ezdwg reads the AutoCAD drawings. It is a Rust core behind a Python API, so
# it ships as a compiled wheel -- and there is only a win_amd64 one. On an
# ARM64 Windows machine this install is where the build stops, which is the
# same reason -Arch x64 is what you should be building anyway.
& $Python -m pip install --quiet --upgrade anthropic pyinstaller ezdwg pypdf
if ($LASTEXITCODE -ne 0) { throw "pip install failed -- is '$Python' the right interpreter?" }

# PyInstaller is unreliable building from a network share; work in a local copy.
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory $work | Out-Null
foreach ($d in @("hwwriter", "docs")) { Copy-Item (Join-Path $src $d) $work -Recurse }
New-Item -ItemType Directory (Join-Path $work "shells") | Out-Null
Copy-Item (Join-Path $src "shells\Starter Shell.hw") (Join-Path $work "shells\")
Copy-Item (Join-Path $src "lutron_builder.py") $work
Get-ChildItem (Join-Path $work "hwwriter\__pycache__") -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force

Set-Location $work
# The shell and the docs folder are DATA: the extraction brief and the reference
# catalogues are read at runtime, so they must be bundled, not just imported.
& $Python -m PyInstaller --onefile --name "Lutron Builder" `
  --add-data "shells\Starter Shell.hw;shells" `
  --add-data "docs;docs" `
  --collect-all anthropic `
  --collect-all ezdwg `
  --collect-all pypdf `
  lutron_builder.py
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

$out = Join-Path $src "dist"
New-Item -ItemType Directory -Force $out | Out-Null
Copy-Item (Join-Path $work "dist\Lutron Builder.exe") (Join-Path $out "Lutron Builder.exe") -Force
Write-Host ("BUILT " + (Get-Item (Join-Path $out "Lutron Builder.exe")).Length + " bytes -> dist\")
