#!/usr/bin/with-contenv bashio
set -e
mkdir -p /data
bashio::log.info "Avvio Irrigazione Centralizzata 0.2.7"
uvicorn main:app --host 0.0.0.0 --port 8099 --proxy-headers --forwarded-allow-ips="*" &
ADMIN_PID=$!
uvicorn operator:app --host 0.0.0.0 --port 8100 --proxy-headers --forwarded-allow-ips="*" &
OPERATOR_PID=$!
trap 'kill $ADMIN_PID $OPERATOR_PID 2>/dev/null || true' TERM INT
wait -n $ADMIN_PID $OPERATOR_PID
STATUS=$?
kill $ADMIN_PID $OPERATOR_PID 2>/dev/null || true
wait || true
exit $STATUS
