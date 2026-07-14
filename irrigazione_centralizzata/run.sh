#!/usr/bin/with-contenv bashio
set -e

mkdir -p /data
bashio::log.info "Avvio Irrigazione Centralizzata"
exec uvicorn main:app --host 0.0.0.0 --port 8099 --proxy-headers --forwarded-allow-ips="*"
