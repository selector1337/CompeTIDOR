#!/usr/bin/env bash
set -u

STATE_FILE=/run/competidor-healthcheck.failures
HEALTH_URL="${COMPETIDOR_LIVENESS_URL:-http://127.0.0.1:${PORT:-8770}/api/live}"

response="$(curl --silent --show-error --fail --connect-timeout 3 --max-time 15 "$HEALTH_URL" 2>&1)"
curl_status=$?
if (( curl_status == 0 )) && printf '%s' "$response" | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'; then
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
logger -t competidor-healthcheck "Falha de saúde ${failures}/3 em ${HEALTH_URL}; curl=${curl_status}; resposta=${response:0:300}"

if (( failures >= 3 )); then
  logger -t competidor-healthcheck "Estado antes do reinício: $(systemctl show competidor.service -p MemoryCurrent -p MemoryHigh -p MemoryMax -p TasksCurrent --value | tr '\n' ' ')"
  logger -t competidor-healthcheck "CompeTIDOR permaneceu indisponível; reiniciando somente o serviço competidor"
  systemctl restart competidor.service
  printf '0\n' > "$STATE_FILE"
fi
