#!/bin/sh
# Generate a self-signed TLS cert for the PersonalClaw web proxy if one isn't
# already present. Runs from the official nginx image's docker-entrypoint.d/
# hook chain (the `10-` prefix runs before `20-envsubst-on-templates.sh`), so
# the cert exists before nginx loads the rendered config.
#
# The stack ships HTTPS + HTTP/2 out of the box with this throwaway cert. To use
# a real certificate, mount it over /etc/nginx/certs/personalclaw.{crt,key}
# (this script then leaves it untouched).
set -eu

CERT_DIR=/etc/nginx/certs
CRT="$CERT_DIR/personalclaw.crt"
KEY="$CERT_DIR/personalclaw.key"

if [ -f "$CRT" ] && [ -f "$KEY" ]; then
    echo "self-signed-cert: existing cert found at $CRT — leaving it in place"
    exit 0
fi

mkdir -p "$CERT_DIR"
echo "self-signed-cert: generating a self-signed cert at $CRT"
openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$KEY" -out "$CRT" -days 3650 \
    -subj "/CN=personalclaw.local" \
    -addext "subjectAltName=DNS:localhost,DNS:personalclaw.local,IP:127.0.0.1" \
    2>/dev/null
chmod 600 "$KEY"
