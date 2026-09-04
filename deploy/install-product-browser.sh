#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${COMPETIDOR_APP_DIR:-/opt/competidor}"
BROWSER_DIR="${COMPETIDOR_BROWSER_DIR:-/var/lib/competidor/ms-playwright}"

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  echo "Python da aplicação não encontrado em $APP_DIR/.venv/bin/python" >&2
  exit 1
fi

install -d -m 0750 -o www-data -g www-data "$BROWSER_DIR"
PLAYWRIGHT_BROWSERS_PATH="$BROWSER_DIR" "$APP_DIR/.venv/bin/python" -m playwright install --with-deps chromium
chown -R www-data:www-data "$BROWSER_DIR"
chmod -R u=rwX,g=rX,o= "$BROWSER_DIR"

echo "Chromium do importador instalado em $BROWSER_DIR"
