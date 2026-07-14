#!/usr/bin/with-contenv bashio
set -euo pipefail

mkdir -p /data
bashio::log.info "Avvio Irrigazione Centralizzata 0.3.2"

cd /app

python3 -m uvicorn main:app \
  --host 0.0.0.0 \
  --port 8099 \
  --proxy-headers \
  --forwarded-allow-ips="*" &
ADMIN_PID=$!

python3 -m uvicorn operator_portal:app \
  --host 0.0.0.0 \
  --port 8100 \
  --proxy-headers \
  --forwarded-allow-ips="*" &
OPERATOR_PID=$!

shutdown() {
  kill "$ADMIN_PID" "$OPERATOR_PID" 2>/dev/null || true
  wait "$ADMIN_PID" "$OPERATOR_PID" 2>/dev/null || true
}
trap shutdown TERM INT EXIT

wait -n "$ADMIN_PID" "$OPERATOR_PID"