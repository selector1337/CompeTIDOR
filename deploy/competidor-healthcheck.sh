#!/usr/bin/env bash
set -u

STATE_FILE=/run/competidor-healthcheck.failures
HEALTH_URL="${COMPETIDOR_HEALTH_URL:-http://127.0.0.1:${PORT:-8770}/api/health}"

response="$(curl --silent --show-error --fail --max-time 8 "$HEALTH_URL" 2>/dev/null || true)"
if printf '%s' "$response" | grep -q '"ok":true'; then
  printf '0\n' > "$STATE_FILE"
  exit 0
fi

failures=0
if [[ -r "$STATE_FILE" ]]; then
  read -r failures < "$STATE_FILE" || failures=0
fi
[[ "$failures" =~ ^[0-9]+$ ]] || failures=0
failures=$((failures + 1))
printf '%s\n' "$failures" > "$STATE_FILE"
logger -t competidor-healthcheck "Falha de saúde ${failures}/3 em ${HEALTH_URL}"

if (( failures >= 3 )); then
  logger -t competidor-healthcheck "CompeTIDOR permaneceu indisponível; reiniciando somente o serviço competidor"
  systemctl restart competidor.service
  printf '0\n' > "$STATE_FILE"
fi
