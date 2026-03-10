#!/bin/bash
# Download a fresh copy of the production database from Fly.io.
# Usage: ./scripts/download_prod_db.sh
#
# Creates: backups/prod_YYYY-MM-DD_HHMMSS.db  (timestamped archive)
#          data/dev.db                         (ready for local use)

set -e

FLYCTL="${HOME}/.fly/bin/flyctl"
if [ ! -f "$FLYCTL" ]; then
  FLYCTL="flyctl"  # fall back to PATH
fi

TIMESTAMP=$(date +"%Y-%m-%d_%H%M%S")
mkdir -p backups

echo "→ Downloading production database from Fly.io..."
"$FLYCTL" sftp get /data/rent.db "backups/prod_${TIMESTAMP}.db"

echo "→ Copying to data/dev.db and data/rent.db for local use..."
cp "backups/prod_${TIMESTAMP}.db" data/dev.db
cp "backups/prod_${TIMESTAMP}.db" data/rent.db

echo ""
echo "✓ Done."
echo "  Archive  : backups/prod_${TIMESTAMP}.db"
echo "  dev.db   : data/dev.db  (used by ./scripts/run_dev.sh)"
echo "  rent.db  : data/rent.db (used by flask run directly)"
echo ""
echo "Run the app locally with:"
echo "  ./scripts/run_dev.sh"
