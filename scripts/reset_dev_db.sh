#!/bin/bash
# Reset data/dev.db back to the latest downloaded production snapshot.
# Use this when you've messed up the test database and want a clean slate.
#
# Usage: ./scripts/reset_dev_db.sh

set -e

LATEST=$(ls -t backups/prod_*.db 2>/dev/null | head -1)

if [ -z "$LATEST" ]; then
  echo "ERROR: No backup found in backups/."
  echo "Run ./scripts/download_prod_db.sh first."
  exit 1
fi

echo "→ Resetting data/dev.db from: $LATEST"
cp "$LATEST" data/dev.db
echo "✓ Done. data/dev.db restored."
