#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test "$(id -u)" = 0 || { echo 'Execute com sudo bash deploy/install-recovery.sh'; exit 1; }
test -x /usr/bin/sudo
test -x /usr/bin/systemctl
install -d -o root -g root -m 0755 /usr/local/lib/competidor-recovery
install -o root -g root -m 0644 recovery_service.py recovery.html /usr/local/lib/competidor-recovery/
tmp_rule=$(mktemp)
trap 'rm -f "$tmp_rule"' EXIT
printf '%s\n' 'www-data ALL=(root) NOPASSWD: /usr/bin/systemctl restart competidor.service' > "$tmp_rule"
visudo -cf "$tmp_rule"
install -o root -g root -m 0440 "$tmp_rule" /etc/sudoers.d/competidor-recovery
install -m 0644 deploy/competidor-recovery.service /etc/systemd/system/
install -m 0644 deploy/nginx-recovery.inc /etc/nginx/competidor-recovery.inc
systemctl daemon-reload
systemctl enable --now competidor-recovery
echo 'Inclua include /etc/nginx/competidor-recovery.inc; no bloco HTTPS do CompeTIDOR. Depois: nginx -t && systemctl reload nginx'
