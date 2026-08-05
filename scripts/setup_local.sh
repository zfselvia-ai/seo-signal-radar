#!/usr/bin/env bash
# One-time local setup for the SEO Signal Radar.
# Installs deps, checks an LLM key is present, and runs a real daily digest.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env — open it and paste ONE API key (Kimi, Claude or GPT), then re-run."
  exit 0
fi

if ! grep -qE '^(SCNET|MOONSHOT|ANTHROPIC|OPENAI)_API_KEY=.+' .env; then
  echo "ERROR: paste one API key into .env first." >&2
  echo "  SCNet (GLM-5.2) -> SCNET_API_KEY      (https://www.scnet.cn)" >&2
  echo "  Kimi            -> MOONSHOT_API_KEY   (https://platform.moonshot.ai)" >&2
  echo "  Claude          -> ANTHROPIC_API_KEY  (https://console.anthropic.com)" >&2
  echo "  GPT             -> OPENAI_API_KEY     (https://platform.openai.com)" >&2
  exit 1
fi

echo "Installing dependencies..."
pip3 install -r requirements.txt

echo "Running a real daily digest (uses your API key — costs a few cents)..."
python3 main.py daily

echo
echo "Done. Open dashboard/index.html in a browser and check reports/daily/ for today's file."
echo "From now on, run it yourself with: python3 main.py daily"
echo "If sources look thin, run: python3 main.py doctor"
