# Base image digest-pinned, resolved 2026-08-01. Unlike the X-Road
# images, a pinned base stops receiving security patches until someone
# re-pins it -- re-pin periodically, not just when X-Road bumps.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt && apt-get update \
    && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY app.py entrypoint.sh ./
USER nobody
# :8000 plain, :8443 TLS -- see entrypoint.sh for where the certificate
# comes from (the Test CA's own /testca/sign, docs/production-delta.md row 18).
EXPOSE 8000 8443
# Both listeners, and the TLS one verified against the Test CA bundle
# entrypoint.sh downloaded -- deliberately NOT `curl -k`. A dead or
# untrusted :8443 has to report unhealthy, otherwise this container passes
# its own probe while every https spec_url pointed at it fails. The
# `-f "$CA..."` guard keeps the probe honest on the degraded path
# entrypoint.sh documents (no CA reachable at boot -> :8000 only).
HEALTHCHECK --interval=5s --retries=12 CMD curl -f http://localhost:8000/v1/health \
  && { [ ! -s /tmp/tls/server.crt ] \
       || curl -f --cacert /tmp/tls/ca.pem --resolve "$(hostname):8443:127.0.0.1" \
               "https://$(hostname):8443/v1/health"; } || exit 1
ENTRYPOINT ["sh", "/app/entrypoint.sh"]
