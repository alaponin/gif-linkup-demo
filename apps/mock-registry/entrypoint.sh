#!/bin/sh
# Two listeners, one process tree, no supervisor: plain HTTP on :8000 (the
# image's own healthcheck, and every caller that has not moved) and HTTPS on
# :8443 once this container holds a server certificate.
#
# WHERE THE CERTIFICATE COMES FROM (docs/production-delta.md row 18). No new
# issuance mechanism had to be invented for this: the Test CA already issues
# server certificates through the same HTTP API the Security Servers use for
# their AUTH certs (hurl/templates/fragments/SS_AUTH_KEY_CSR.hurl.tmpl ->
# POST /testca/sign, type=auth). Verified live against the running `ca`
# container before this was written -- its CA.cnf's `auth_ext` is
#     basicConstraints = CA:FALSE
#     keyUsage = critical, digitalSignature, keyEncipherment, ...
#     extendedKeyUsage = clientAuth, serverAuth
# and its sign_req.sh copies a CSR's subjectAltName straight through into the
# issued certificate whenever type=auth. So a CSR carrying
# `subjectAltName=DNS:app-plr` comes back as a genuine serverAuth certificate
# for that name -- exactly what a TLS client verifying a hostname needs, and
# nothing here is a special case the CA does not already do for X-Road.
#
# DEMO ONLY, twice over: the Test CA signs whatever anyone asks it to, and
# this container asks it unauthenticated. A real deployment gets its server
# certificate from an accredited CA and mounts it -- set TLS_CERT/TLS_KEY to
# a mounted pair and this script never talks to a CA at all.
set -e

TLS_DIR=${TLS_DIR:-/tmp/tls}
TLS_CERT=${TLS_CERT:-$TLS_DIR/server.crt}
TLS_KEY=${TLS_KEY:-$TLS_DIR/server.key}
TLS_NAME=${TLS_NAME:-$(hostname)}
TESTCA_SIGN_URL=${TESTCA_SIGN_URL:-http://ca:8888/testca/sign}
TESTCA_CA_URL=${TESTCA_CA_URL:-http://ca:8888/testca/certs/ca.cert.pem}
# The CA bundle this container's own healthcheck verifies :8443 against, so
# that probe is a real verification and not a `curl -k`.
CA_BUNDLE=${CA_BUNDLE:-$TLS_DIR/ca.pem}

if [ ! -f "$TLS_CERT" ]; then
  mkdir -p "$TLS_DIR"
  openssl req -new -newkey rsa:2048 -nodes \
    -keyout "$TLS_KEY" -out "$TLS_DIR/server.csr" \
    -subj "/O=X-Road Test/CN=$TLS_NAME" \
    -addext "subjectAltName=DNS:$TLS_NAME" >/dev/null 2>&1

  # `ca` has no depends_on relationship to this service and may still be
  # initialising its own key material. This retry is the only thing this
  # entrypoint ever waits for; ~2 minutes, then it gives up loudly and
  # serves plain HTTP alone rather than wedging the demo.
  i=0
  until curl -fsS -o "$TLS_CERT" -X POST "$TESTCA_SIGN_URL" \
             -F type=auth \
             -F "certreq=@$TLS_DIR/server.csr;filename=auth.csr.pem" \
       && curl -fsS -o "$CA_BUNDLE" "$TESTCA_CA_URL"; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
      echo "mock-registry: Test CA at $TESTCA_SIGN_URL never answered -- :8443 disabled, :8000 only" >&2
      rm -f "$TLS_CERT"
      break
    fi
    sleep 2
  done
fi

if [ -s "$TLS_CERT" ]; then
  # ponytail: backgrounded, not supervised -- if the TLS listener dies the
  # container keeps serving :8000. The image's HEALTHCHECK probes BOTH
  # ports, so that state reports unhealthy rather than passing silently;
  # a supervisor would be a second process to configure for the same signal.
  uvicorn app:app --host 0.0.0.0 --port 8443 \
    --ssl-keyfile "$TLS_KEY" --ssl-certfile "$TLS_CERT" &
fi

exec uvicorn app:app --host 0.0.0.0 --port 8000
