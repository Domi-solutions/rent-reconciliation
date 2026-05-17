#!/bin/bash
# Start cloudflared tunnel + dev server for local Daraja sandbox testing.
# All activity goes to data/dev.db — production is never touched.

set -e

DEV_DB="$(cd "$(dirname "$0")/.." && pwd)/data/dev.db"
if [ ! -f "$DEV_DB" ]; then
  echo "ERROR: data/dev.db not found. Run ./scripts/download_prod_db.sh first."
  exit 1
fi

echo "Starting cloudflared tunnel..."
TUNNEL_LOG=/tmp/cloudflared_tunnel.log
cloudflared tunnel --url http://localhost:5050 > "$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

# Wait for tunnel URL to appear in log
echo "Waiting for tunnel URL..."
TUNNEL_URL=""
for i in $(seq 1 30); do
  TUNNEL_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1)
  if [ -n "$TUNNEL_URL" ]; then
    break
  fi
  sleep 1
done

if [ -z "$TUNNEL_URL" ]; then
  echo "ERROR: Could not get tunnel URL. Check $TUNNEL_LOG"
  kill $TUNNEL_PID 2>/dev/null
  exit 1
fi

CALLBACK_URL="${TUNNEL_URL}/inbound/payment/mpesa"
echo ""
echo "========================================"
echo "  Tunnel:   $TUNNEL_URL"
echo "  Callback: $CALLBACK_URL"
echo "  Database: data/dev.db (production SAFE)"
echo "========================================"
echo ""

# Export env for the dev server
export DATABASE_PATH="$DEV_DB"
export DARAJA_CALLBACK_URL="$CALLBACK_URL"
export FLASK_ENV=development
export SECRET_KEY=dev-local-secret-not-for-production
unset ADMIN_PASSWORD
unset VIEWER_PASSWORD
unset CARETAKER_PASSWORD

# Load .env for Daraja + AT credentials
ENV_FILE="$(dirname "$0")/../.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

# Override callback with the tunnel URL (must come after .env sourcing)
export DARAJA_CALLBACK_URL="$CALLBACK_URL"

cleanup() {
  echo ""
  echo "Shutting down tunnel..."
  kill $TUNNEL_PID 2>/dev/null
}
trap cleanup EXIT

echo "Starting dev server on http://localhost:5050"
echo "(Press Ctrl+C to stop both tunnel and server)"
echo ""
"$(dirname "$0")/../venv/bin/python" "$(dirname "$0")/../app.py"
