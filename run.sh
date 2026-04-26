#!/usr/bin/env bash
# ════════════════════════════════════════════════════
# run.sh – InstaShift launcher script
# Usage: bash run.sh
# ════════════════════════════════════════════════════
set -euo pipefail

# ── 1. Check Python version ──────────────────────────
REQUIRED_PY="3.10"
PYTHON=$(command -v python3 || command -v python)
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")

if [[ "$(printf '%s\n' "$REQUIRED_PY" "$PY_VER" | sort -V | head -n1)" != "$REQUIRED_PY" ]]; then
  echo "❌  Python $REQUIRED_PY+ required (found $PY_VER)" >&2
  exit 1
fi
echo "✅  Python $PY_VER detected"

# ── 2. Virtual environment ───────────────────────────
if [[ ! -d ".venv" ]]; then
  echo "📦  Creating virtual environment..."
  "$PYTHON" -m venv .venv
fi

source .venv/bin/activate
echo "🔧  Virtual environment activated"

# ── 3. Install / upgrade dependencies ───────────────
echo "📥  Installing dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo "✅  Dependencies installed"

# ── 4. Ensure .env exists ────────────────────────────
if [[ ! -f ".env" ]]; then
  echo "⚠️   .env not found – copying .env.example"
  cp .env.example .env
  echo "✏️   Please fill in your credentials in .env and re-run."
  exit 1
fi

# ── 5. Launch the bot ────────────────────────────────
echo "🚀  Starting InstaShift..."
exec "$PYTHON" -m bot.main
