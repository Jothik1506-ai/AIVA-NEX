<#
.SYNOPSIS
  NEXAIVA "Install Agent" for Aiva Nex Agent (SIH26171): one command sets up
  the local server runtime, then runs the setup verifier.

.DESCRIPTION
  Idempotent - safe to re-run; finished steps are detected and skipped.
    1. find Python 3.10-3.12 (py launcher, then python on PATH)
    2. create server\.venv (reused if it already has a supported Python)
    3. pip install server\requirements.txt (or requirements-dev.txt with -Dev)
    4. verify / download the spaCy model en_core_web_sm
    5. verify the RapidOCR ONNX models (bundled in the wheel) load on CPU
    6. create server\.aiva_token if missing (value NOT printed unless -ShowToken)
    7. run scripts\verify_setup.py (offline unless -CheckServer)

.PARAMETER Dev
  Install requirements-dev.txt (pytest, httpx, pillow) as well.
.PARAMETER ShowToken
  Print the shared token once at the end (to paste into the extension).
.PARAMETER CheckServer
  Also run the live-server checks (server must already be running).
.PARAMETER SkipVerify
  Do not run scripts\verify_setup.py at the end.

.EXAMPLE
  .\setup.ps1 -Dev
  powershell -ExecutionPolicy Bypass -File .\setup.ps1 -ShowToken
#>
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$ShowToken,
    [switch]$CheckServer,
    [switch]$SkipVerify
)

# 'Continue', not 'Stop': in Windows PowerShell 5.1 a native command's stderr
# redirected with 2>$null becomes an error record, which 'Stop' would turn into
# a crash. Every native call below checks $LASTEXITCODE explicitly instead.
$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot
$Server = Join-Path $Root 'server'
$Venv = Join-Path $Server '.venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
$TokenFile = Join-Path $Server '.aiva_token'
$script:Step = 0

function Write-Step([string]$msg) { $script:Step++; Write-Host ""; Write-Host "[$script:Step/7] $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "  OK    $msg" -ForegroundColor Green }
function Write-Info([string]$msg) { Write-Host "  ..    $msg" -ForegroundColor Gray }
function Write-Warn2([string]$msg){ Write-Host "  WARN  $msg" -ForegroundColor Yellow }
function Stop-Setup([string]$msg) { Write-Host "  FAIL  $msg" -ForegroundColor Red; exit 1 }

# Returns "3.11.9" for a python command (array: exe + args), or $null.
function Get-PyVersion([string[]]$cmd) {
    try {
        $exe = $cmd[0]; $rest = @(); if ($cmd.Count -gt 1) { $rest = $cmd[1..($cmd.Count - 1)] }
        $v = & $exe @rest -c "import sys;print('%d.%d.%d' % sys.version_info[:3])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { return ($v | Select-Object -Last 1).Trim() }
    } catch { }
    return $null
}

function Test-Supported([string]$ver) {
    if (-not $ver) { return $false }
    $p = $ver.Split('.'); $maj = [int]$p[0]; $min = [int]$p[1]
    return ($maj -eq 3 -and $min -ge 10 -and $min -le 12)
}

Write-Host "Aiva Nex Agent - NEXAIVA install agent" -ForegroundColor White
Write-Host "Repo: $Root"

# ---------------------------------------------------------------- 1. Python
Write-Step "Python 3.10-3.12"
$PyCmd = $null; $PyVer = $null
$candidates = @(@('py', '-3.11'), @('py', '-3.12'), @('py', '-3.10'), @('python'), @('python3'))
foreach ($c in $candidates) {
    if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
    $v = Get-PyVersion $c
    if (Test-Supported $v) { $PyCmd = $c; $PyVer = $v; break }
}
if (-not $PyCmd) { Stop-Setup "No Python 3.10-3.12 found. Install 3.11 from python.org (tick 'Add to PATH') and re-run." }
Write-Ok "$($PyCmd -join ' ') -> Python $PyVer"

# ---------------------------------------------------------------- 2. venv
Write-Step "Virtual environment (server\.venv)"
$venvVer = $null
if (Test-Path $VenvPy) { $venvVer = Get-PyVersion @($VenvPy) }
if (Test-Supported $venvVer) {
    Write-Ok "reusing existing venv (Python $venvVer)"
} else {
    if (Test-Path $Venv) { Stop-Setup "server\.venv exists but its Python ($venvVer) is unusable/unsupported. Delete server\.venv and re-run." }
    Write-Info "creating server\.venv ..."
    $exe = $PyCmd[0]; $rest = @(); if ($PyCmd.Count -gt 1) { $rest = $PyCmd[1..($PyCmd.Count - 1)] }
    & $exe @rest -m venv $Venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) { Stop-Setup "venv creation failed" }
    Write-Ok "created (Python $(Get-PyVersion @($VenvPy)))"
}

# ---------------------------------------------------------------- 3. requirements
$req = if ($Dev) { 'requirements-dev.txt' } else { 'requirements.txt' }
Write-Step "pip install -r server\$req"
Write-Info "first run downloads ~150 MB (FastAPI, spaCy + model, RapidOCR, OpenCV); re-runs are quick"
& $VenvPy -m pip install --disable-pip-version-check -q -r (Join-Path $Server $req)
if ($LASTEXITCODE -ne 0) { Stop-Setup "pip install failed (see output above)" }
Write-Ok "requirements satisfied"

# ---------------------------------------------------------------- 4. spaCy model
Write-Step "spaCy model en_core_web_sm (names/addresses NER)"
& $VenvPy -c "import spacy; spacy.load('en_core_web_sm')" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Info "model not loadable - downloading ..."
    & $VenvPy -m spacy download en_core_web_sm
    & $VenvPy -c "import spacy; spacy.load('en_core_web_sm')" 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Warn2 "model still missing - server will run NER rules-only" } else { Write-Ok "downloaded and loads" }
} else {
    Write-Ok "loads"
}

# ---------------------------------------------------------------- 5. RapidOCR
Write-Step "RapidOCR ONNX models (visual perception)"
# Single quotes only inside Python code: PowerShell 5.1 strips embedded double
# quotes from native-command arguments.
$ocrProbe = "import pathlib, rapidocr_onnxruntime as r; " +
    "models = sorted(p.name for p in pathlib.Path(r.__file__).parent.rglob('*.onnx')); " +
    "from rapidocr_onnxruntime import RapidOCR; RapidOCR(); " +
    "print(str(len(models)) + ' ONNX files: ' + ', '.join(models)) if len(models) >= 2 else exit(3)"
$ocr = & $VenvPy -c $ocrProbe 2>$null
if ($LASTEXITCODE -eq 0 -and $ocr) {
    Write-Ok ("engine loads; models: " + ($ocr | Select-Object -Last 1))
} else {
    Write-Warn2 "RapidOCR did not load - /perceive (Visual mode) will be disabled; DOM mode still works"
}

# ---------------------------------------------------------------- 6. token
Write-Step "Shared token (server\.aiva_token)"
if ($env:AIVA_SHARED_TOKEN) {
    Write-Ok "AIVA_SHARED_TOKEN is set in this shell - the server will use it instead of the file"
} elseif ((Test-Path $TokenFile) -and ((Get-Content $TokenFile -Raw).Trim().Length -gt 0)) {
    Write-Ok "exists (kept; value not shown)"
} else {
    # Same format security.py creates: secrets.token_urlsafe(32) + newline, UTF-8 without BOM.
    & $VenvPy -c "import secrets,sys,pathlib; p=pathlib.Path(sys.argv[1]); p.write_text(secrets.token_urlsafe(32)+'\n', encoding='utf-8')" $TokenFile
    if ($LASTEXITCODE -ne 0) { Stop-Setup "could not write $TokenFile" }
    Write-Ok "created (git-ignored; value not shown - use -ShowToken or open the file yourself)"
}
if ($ShowToken) {
    if ($env:AIVA_SHARED_TOKEN) { $tok = $env:AIVA_SHARED_TOKEN } else { $tok = (Get-Content $TokenFile -Raw).Trim() }
    Write-Host "  TOKEN $tok" -ForegroundColor Magenta
    Write-Host "        paste into the side panel > Settings. Do not share or commit it." -ForegroundColor Magenta
}
if (-not $env:AIVA_EXTENSION_ORIGIN) {
    Write-Warn2 "AIVA_EXTENSION_ORIGIN not set - after loading the extension run:"
    Write-Host '        $env:AIVA_EXTENSION_ORIGIN = "chrome-extension://<your-extension-id>"' -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 7. verify
Write-Step "Verify (scripts\verify_setup.py)"
if ($SkipVerify) {
    Write-Info "skipped (-SkipVerify)"
} else {
    $vargs = @((Join-Path $Root 'scripts\verify_setup.py'))
    if (-not $CheckServer) { $vargs += '--no-server' }
    & $VenvPy @vargs
    if ($LASTEXITCODE -ne 0) { Stop-Setup "verification reported FAIL lines (above)" }
}

Write-Host ""
Write-Host "Done. Next: start the server and follow NEXAIVA\SETUP_GUIDE.md" -ForegroundColor Green
Write-Host "  server\.venv\Scripts\python server\main.py"
