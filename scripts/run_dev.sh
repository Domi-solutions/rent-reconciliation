#!/bin/bash
# Run the app locally against a TEST copy of the database.
# Safe to stress-test — production data is untouched.
#
# First time setup:
#   ./scripts/download_prod_db.sh   ← pulls a fresh copy from Fly.io
#
# Subsequent runs:
#   ./scripts/run_dev.sh            ← starts the app on localhost:5000

set -e

DEV_DB="$(dirname "$0")/../data/dev.db"

if [ ! -f "$DEV_DB" ]; then
  echo "ERROR: data/dev.db not found."
  echo ""
  echo "To create it, run one of:"
  echo "  ./scripts/download_prod_db.sh   (copies production data)"
  echo "  cp data/rent_prod_copy.db data/dev.db   (if you have a local copy)"
  exit 1
fi

# Absolute path so Flask can find it regardless of cwd
export DATABASE_PATH="$(cd "$(dirname "$DEV_DB")" && pwd)/$(basename "$DEV_DB")"

# Dev mode — no password required to log in
unset ADMIN_PASSWORD
unset VIEWER_PASSWORD
unset CARETAKER_PASSWORD

export FLASK_ENV=development
export SECRET_KEY=dev-local-secret-not-for-production

echo "========================================"
echo "  LOCAL DEV — using data/dev.db"
echo "  Production database is NOT affected"
echo "  Admin password: disabled (dev mode)"
echo "  URL: http://localhost:5000"
echo "========================================"
echo ""

./venv/bin/python app.py
