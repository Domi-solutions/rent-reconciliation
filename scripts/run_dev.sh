#!/bin/bash
# Run the app locally against a TEST copy of the database.
# Safe to stress-test — production data is untouched.
#
# First time setup:
#   ./scripts/download_prod_db.sh   ← pulls a fresh copy from Fly.io
#
# Subsequent runs:
#   ./scripts/run_dev.sh            ← starts the app on localhost:5001

set -e

DEV_DB="$(dirname "$0")/../data/dev.db"

if [ ! -f "$DEV_DB" ]; then
  echo "→ data/dev.db not found — will be created fresh on startup."
fi

# Absolute path so Flask can find it regardless of cwd
export DATABASE_PATH="$(cd "$(dirname "$DEV_DB")" && pwd)/$(basename "$DEV_DB")"

# Dev passwords — use org email+password to log in, or ADMIN_PASSWORD below as master key
export ADMIN_PASSWORD=dev
unset VIEWER_PASSWORD
unset CARETAKER_PASSWORD

export FLASK_ENV=development
export SECRET_KEY=dev-local-secret-not-for-production

# Email (Mailtrap) — set in .env to get codes delivered to your inbox:
#   SMTP_HOST=sandbox.smtp.mailtrap.io
#   SMTP_PORT=587
#   SMTP_USER=<mailtrap-username>
#   SMTP_PASSWORD=<mailtrap-password>
#   SMTP_FROM=noreply@domi.co.ke
# Without these, OTP codes are logged to /platform/outbox (status: simulated).

# Load .env for third-party credentials (AT_API_KEY, SMTP_*, etc.) — never committed
ENV_FILE="$(dirname "$0")/../.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

echo "========================================"
echo "  LOCAL DEV — using data/dev.db"
echo "  Production database is NOT affected"
echo "  Master key: ADMIN_PASSWORD=dev (or use org email+password)"
echo "  URL: http://localhost:${PORT:-5050}"
echo "========================================"
echo ""

./venv/bin/python app.py
