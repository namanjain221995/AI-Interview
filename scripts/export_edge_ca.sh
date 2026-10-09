#!/usr/bin/env bash
# Exports the root certificate of the edge's internal CA (Caddy "tls internal")
# so LAN test devices can trust https://<host>:3443 instead of clicking
# through a certificate warning. Only the public certificate leaves the
# container; the CA private key stays in the caddydata volume.
#
#   scripts/export_edge_ca.sh                  # -> data/edge-root-ca.crt
#   scripts/export_edge_ca.sh /tmp/ca.crt
set -euo pipefail
# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require docker

out="${1:-$ROOT/data/edge-root-ca.crt}"
service_running edge || die "the edge is not running (make up)"
mkdir -p "$(dirname "$out")"
compose exec -T edge cat /data/caddy/pki/authorities/local/root.crt >"$out" ||
  die "root certificate not found; it is created when the edge first starts with 'tls internal'"
chmod 0644 "$out"

ok "root CA certificate written to $out"
if command -v openssl >/dev/null 2>&1; then
  openssl x509 -in "$out" -noout -subject -enddate -fingerprint -sha256 | sed 's/^/      /'
fi
cat <<'EOF'

Install it as a trusted root on the test device, then open https://<host-ip>:3443
  macOS:    open the file, Keychain Access > System > set "Always Trust"
  Windows:  certutil -addstore -f Root edge-root-ca.crt   (admin shell)
  Linux:    sudo cp edge-root-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates
            (Chrome/Firefox on Linux use their own store: Settings > Certificates > Authorities > Import)
  Android:  Settings > Security > Encryption & credentials > Install a certificate > CA certificate
  iOS:      AirDrop/mail the file, install the profile, then General > About > Certificate Trust Settings
Remove it again when testing is done. Never distribute it to real candidates:
production deployments use publicly trusted certificates (see DEPLOYMENT.md).
EOF
