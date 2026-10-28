#!/bin/sh
# Creates a throwaway local CA and one server certificate for the local stack.
set -eu
out="$1"
if [ -f "$out/server.pem" ] && [ -f "$out/ca.pem" ]; then
  exit 0
fi
# The Alpine mirror occasionally fails transiently; retry before giving up.
attempt=1
until apk add --no-cache openssl >/dev/null; do
  [ "$attempt" -ge 5 ] && exit 1
  attempt=$((attempt + 1))
  sleep 3
done
cd "$out"
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=localizer-local-ca" \
  -keyout ca-key.pem -out ca.pem 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=control-plane.local" \
  -keyout server-key.pem -out server.csr 2>/dev/null
printf 'subjectAltName=DNS:control-plane.local,DNS:providers.local,DNS:localhost\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n' > server.ext
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -days 30 \
  -extfile server.ext -out server.pem 2>/dev/null
rm -f server.csr server.ext ca-key.pem ca.srl
chmod 644 ca.pem server.pem server-key.pem
