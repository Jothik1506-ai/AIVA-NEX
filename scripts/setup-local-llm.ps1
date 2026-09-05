# setup-local-llm.ps1
#
# One-time setup for the OPTIONAL local LLM the server can use (see
# server/main.py -> call_local_llm(), README.md -> "Using a local model").
# Nothing else about the demo requires this - without it, /analyze just
# falls back to the rule-based engine. Run this only if you want the real
# model reasoning instead.
#
# What it does, in order:
#   1. Checks whether Ollama is already installed.
#   2. If not, installs it via winget (asks first - see below).
#   3. Waits for the Ollama service to be reachable.
#   4. Pulls the model the server defaults to (llama3.2:1b), or one you name.
#
# This is a script YOU run, not something the server or extension triggers
# on its own - installing a background service and pulling a ~1.3GB model
# without you asking for it would be a bad thing for software to do quietly.
#
# Usage:
#   ./scripts/setup-local-llm.ps1
#   ./scripts/setup-local-llm.ps1 -Model qwen2.5:0.5b

param(
    [string]$Model = "llama3.2:1b"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Test-CommandExists($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

Write-Step "Checking for Ollama"

if (Test-CommandExists "ollama") {
    Write-Host "Ollama is already installed." -ForegroundColor Green
}
else {
    if (-not (Test-CommandExists "winget")) {
        Write-Host "Ollama isn't installed and winget isn't available on this machine." -ForegroundColor Yellow
        Write-Host "Install it manually from https://ollama.com/download, then re-run this script."
        exit 1
    }

    Write-Host "Ollama isn't installed. This script will install it via winget (Ollama.Ollama)."
    $confirm = Read-Host "Proceed with installing Ollama now? [y/N]"
    if ($confirm -notmatch '^[Yy]') {
        Write-Host "Skipped. Install it manually from https://ollama.com/download when you're ready."
        exit 0
    }

    Write-Step "Installing Ollama via winget"
    winget install --id Ollama.Ollama --accept-package-agreements --accept-source-agreements

    if (-not (Test-CommandExists "ollama")) {
        Write-Host "Ollama was installed but isn't on PATH in this session yet." -ForegroundColor Yellow
        Write-Host "Close and reopen your terminal, then re-run this script to pull the model."
        exit 0
    }
}

Write-Step "Waiting for the Ollama service"

$maxAttempts = 15
$reachable = $false
for ($i = 0; $i -lt $maxAttempts; $i++) {
    try {
        Invoke-WebRequest -Uri "http://localhost:11434/v1/models" -TimeoutSec 2 -UseBasicParsing | Out-Null
        $reachable = $true
        break
    }
    catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $reachable) {
    Write-Host "Ollama doesn't seem to be running yet." -ForegroundColor Yellow
    Write-Host "It usually starts automatically after install / on login. If it still isn't up, run 'ollama serve' in another terminal and re-run this script."
    exit 1
}

Write-Host "Ollama is reachable." -ForegroundColor Green

Write-Step "Pulling model: $Model"
ollama pull $Model

Write-Step "Done"
Write-Host "The server (server/main.py) auto-detects this model - no config needed if you're using the default ($Model)." -ForegroundColor Green
Write-Host "If you pulled a different model, set it before starting the server:"
Write-Host "  `$env:LOCAL_LLM_MODEL = `"$Model`""
Write-Host "Then start the server as usual: cd server; python main.py"
