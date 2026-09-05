#!/usr/bin/env bash
#
# setup-local-llm.sh
#
# One-time setup for the OPTIONAL local LLM the server can use (see
# server/main.py -> call_local_llm(), README.md -> "Using a local model").
# Nothing else about the demo requires this - without it, /analyze just
# falls back to the rule-based engine. Run this only if you want the real
# model reasoning instead.
#
# What it does, in order:
#   1. Checks whether Ollama is already installed.
#   2. If not, installs it via the official install script (asks first).
#   3. Waits for the Ollama service to be reachable.
#   4. Pulls the model the server defaults to (llama3.2:1b), or one you name.
#
# This is a script YOU run, not something the server or extension triggers
# on its own - installing a background service and pulling a ~1.3GB model
# without you asking for it would be a bad thing for software to do quietly.
#
# Usage:
#   ./scripts/setup-local-llm.sh
#   ./scripts/setup-local-llm.sh qwen2.5:0.5b

set -euo pipefail

MODEL="${1:-llama3.2:1b}"

step() { printf "\n==> %s\n" "$1"; }

step "Checking for Ollama"

if command -v ollama >/dev/null 2>&1; then
  echo "Ollama is already installed."
else
  echo "Ollama isn't installed. This script will install it via the official installer:"
  echo "  curl -fsSL https://ollama.com/install.sh | sh"
  read -r -p "Proceed with installing Ollama now? [y/N] " confirm
  if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Skipped. Install it manually from https://ollama.com/download when you're ready."
    exit 0
  fi

  step "Installing Ollama"
  curl -fsSL https://ollama.com/install.sh | sh

  if ! command -v ollama >/dev/null 2>&1; then
    echo "Ollama was installed but isn't on PATH in this session yet."
    echo "Open a new terminal, then re-run this script to pull the model."
    exit 0
  fi
fi

step "Waiting for the Ollama service"

reachable=false
for _ in $(seq 1 15); do
  if curl -fsS "http://localhost:11434/v1/models" >/dev/null 2>&1; then
    reachable=true
    break
  fi
  sleep 2
done

if [ "$reachable" != "true" ]; then
  echo "Ollama doesn't seem to be running yet."
  echo "It usually starts automatically after install. If it still isn't up, run 'ollama serve' in another terminal and re-run this script."
  exit 1
fi

echo "Ollama is reachable."

step "Pulling model: $MODEL"
ollama pull "$MODEL"

step "Done"
echo "The server (server/main.py) auto-detects this model - no config needed if you're using the default ($MODEL)."
echo "If you pulled a different model, set it before starting the server:"
echo "  export LOCAL_LLM_MODEL=\"$MODEL\""
echo "Then start the server as usual: cd server && python main.py"
