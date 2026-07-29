#!/usr/bin/env bash
# Refresh X session cookies for the SEO Signal Radar.
#
# X invalidates the twikit session cookie every so often. When a GitHub Actions
# run fails with an X auth error, run this locally, then paste the printed value
# into the repo's X_COOKIES_JSON secret (Settings > Secrets and variables > Actions).
#
# Requires X_USERNAME / X_EMAIL / X_PASSWORD in your local .env (used only here).
set -euo pipefail
cd "$(dirname "$0")/.."

rm -f cookies.json
echo "Logging in to X to mint a fresh cookie (uses .env credentials)..."
python main.py daily --dry-run >/dev/null 2>&1 || true

if [ ! -f cookies.json ]; then
  echo "ERROR: cookies.json was not created. Check X_USERNAME/X_EMAIL/X_PASSWORD in .env." >&2
  exit 1
fi

echo
echo "=== Copy everything between the lines into the X_COOKIES_JSON GitHub secret ==="
echo "-----------------------------------------------------------------------------"
cat cookies.json
echo
echo "-----------------------------------------------------------------------------"
