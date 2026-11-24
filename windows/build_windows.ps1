<#
.SYNOPSIS
  Builds the Windows installer:  dist\installer\CensusExtractor-Setup-<version>.exe

.DESCRIPTION
  1. Creates an isolated build environment (.venv-build) with the app + PyInstaller
  2. Bundles Python, the app and all dependencies into dist\census-extract\
  3. Smoke-tests the bundled census-extract.exe
  4. Compiles the installer with Inno Setup (installs it via winget if missing)

  Run from anywhere:   powershell -ExecutionPolicy Bypass -File windows\build_windows.ps1
  Or double-click:     windows\build_windows.bat

.PARAMETER SkipInstaller
  Only build the portable app folder (dist\census-extract), skip Inno Setup.

.PARAMETER OneFile
  Build a single self-contained dist\onefile\census-extract.exe (easy to copy or
  share; starts a bit slower because it unpacks itself each run). Skips the installer.

.PARAMETER InstallerOnly
  Skip the Python/PyInstaller steps and only compile the installer from an
  existing dist\census-extract folder (handy after fixing an Inno Setup problem).
#>
[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$OneFile,
    [switch]$InstallerOnly,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

$Exe = Join-Path $Root "dist\census-extract\census-extract.exe"
$DistPath = Join-Path $Root "dist"
if ($OneFile) {
    if ($InstallerOnly) { throw "-OneFile and -InstallerOnly can't be combined." }
    $DistPath = Join-Path $Root "dist\onefile"
    $Exe = Join-Path $DistPath "census-extract.exe"
}
if ($InstallerOnly) {
    if (-not (Test-Path $Exe)) { throw "No existing build found at $Exe. Run without -InstallerOnly first." }
    Write-Host "Using existing app build: dist\census-extract\" -ForegroundColor Green
} else {
# --- 1. Find a suitable Python (3.9+, 64-bit) --------------------------------
Step "Looking for Python 3.9+ (64-bit)"
$check = "import sys; sys.exit(0 if sys.version_info >= (3, 9) and sys.maxsize > 2**32 else 1)"
$candidates = @()
if ($Python) { $candidates += ,@($Python) }
$candidates += ,@("py", "-3")
$candidates += ,@("python")
$PyExe = $null; $PyArgs = @()
foreach ($c in $candidates) {
    $exe = $c[0]; $pre = @($c | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    & $exe @pre -c $check 2>$null
    if ($LASTEXITCODE -eq 0) { $PyExe = $exe; $PyArgs = $pre; break }
}
if (-not $PyExe) {
    throw "64-bit Python 3.9+ not found. Install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH') or run:  winget install Python.Python.3.12"
}
& $PyExe @PyArgs -c "import sys; print('Using Python', sys.version.split()[0], 'at', sys.executable)"

# --- 2. Build environment ------------------------------------------------------
$Venv = Join-Path $Root ".venv-build"
$VPy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VPy)) {
    Step "Creating build environment (.venv-build)"
    Invoke-Checked $PyExe ($PyArgs + @("-m", "venv", $Venv))
}
Step "Installing the app and PyInstaller into the build environment"
Invoke-Checked $VPy @("-m", "pip", "install", "--upgrade", "pip", "--quiet")
Invoke-Checked $VPy @("-m", "pip", "install", ".", "pyinstaller>=6.3", "--quiet")

# --- 3. Bundle with PyInstaller ----------------------------------------------
Step "Bundling the app with PyInstaller (takes a few minutes)"
$Icon = Join-Path $PSScriptRoot "census.ico"
Invoke-Checked $VPy @(
    "-m", "PyInstaller", "--noconfirm", "--clean",
    "--name", "census-extract",
    "--console",
    $(if ($OneFile) { "--onefile" } else { "--onedir" }),
    "--icon", $Icon,
    "--distpath", $DistPath,
    "--workpath", (Join-Path $Root "build"),
    "--specpath", (Join-Path $Root "build"),
    "--paths", $Root,
    "--add-data", "$(Join-Path $Root 'census_extractor');census_extractor",
    "--collect-all", "streamlit",
    "--collect-all", "altair",
    "--copy-metadata", "streamlit",
    "--copy-metadata", "pandas",
    "--hidden-import", "census_extractor.app",
    "--exclude-module", "tkinter",
    "--exclude-module", "pytest",
    (Join-Path $PSScriptRoot "launcher.py")
)

Step "Smoke-testing $Exe"
Invoke-Checked $Exe @("--help")
& $Exe "init" (Join-Path $env:TEMP "census_smoke_test.yaml") "--force" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Bundled exe failed the 'init' smoke test." }
if ($OneFile) {
    Write-Host "`nDone! Single-file app: $Exe" -ForegroundColor Green
    Write-Host "Copy it anywhere and double-click it - no install or Python needed."
} else {
    Write-Host "Portable app ready: dist\census-extract\  (run census-extract.exe)" -ForegroundColor Green
}

}

if ($SkipInstaller -or $OneFile) { exit 0 }

# --- 4. Inno Setup -------------------------------------------------------------
function Find-Iscc {
    $paths = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    foreach ($p in $paths) { if ($p -and (Test-Path $p)) { return $p } }
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}
function Install-InnoSetup {
    # 1) winget, if it works on this PC
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Step "Inno Setup not found - trying winget"
        try {
            winget install --id JRSoftware.InnoSetup -e --accept-source-agreements --accept-package-agreements --silent
        } catch { }
        if (Find-Iscc) { return }
        Write-Host "winget didn't work - downloading Inno Setup directly instead." -ForegroundColor Yellow
    }
    # 2) direct download from the official site, installed silently for this user only
    Step "Downloading Inno Setup from jrsoftware.org"
    $setupExe = Join-Path $env:TEMP "innosetup-installer.exe"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri "https://jrsoftware.org/download.php/is.exe" -OutFile $setupExe -UseBasicParsing
    Step "Installing Inno Setup (current user, silent)"
    $p = Start-Process -FilePath $setupExe -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER" -Wait -PassThru
    if ($p.ExitCode -ne 0) { Write-Host "Inno Setup installer exited with code $($p.ExitCode)" -ForegroundColor Yellow }
    Remove-Item $setupExe -ErrorAction SilentlyContinue
}

$Iscc = Find-Iscc
if (-not $Iscc) {
    try { Install-InnoSetup } catch { Write-Host "Automatic install failed: $_" -ForegroundColor Yellow }
    $Iscc = Find-Iscc
}
if (-not $Iscc) {
    throw "Inno Setup 6 could not be installed automatically. Install it by hand from https://jrsoftware.org/isdl.php, then run:  build_windows.bat -InstallerOnly"
}
Write-Host "Using Inno Setup: $Iscc"

$Version = (Select-String -Path (Join-Path $Root "census_extractor\__init__.py") -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Step "Compiling installer v$Version with Inno Setup"
Invoke-Checked $Iscc @("/Qp", "/DAppVersion=$Version", (Join-Path $PSScriptRoot "installer.iss"))

$Setup = Join-Path $Root "dist\installer\CensusExtractor-Setup-$Version.exe"
Write-Host "`nDone! Installer: $Setup" -ForegroundColor Green
