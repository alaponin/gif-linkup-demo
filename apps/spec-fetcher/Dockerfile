# KP2 spec fetcher -- the credential-free service apps/join-api/validate.py
# delegates every applicant-controlled URL fetch to (docs/production-delta.md
# row 41; see app.py's own docstring for the full design). Mirrors apps/
# console's Dockerfile pattern -- the smallest of this pack's app images,
# not apps/join-api's (which also carries hurl, git and postgresql-client
# for jobs this service never runs).
# Base image digest-pinned, resolved 2026-08-01 -- same digest as every
# other apps/*/Dockerfile in this pack. Unlike the X-Road images, a pinned
# base stops receiving security patches until someone re-pins it -- re-pin
# periodically, not just when X-Road bumps.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY app.py .
# No volumes, no ENV secrets (docker-compose.yml's spec-fetcher service
# declares none) -- USER nobody is not defence against a mount this image
# never gets, it is one more thing an SSRF payload lands against and finds
# nothing.
USER nobody
EXPOSE 8000
HEALTHCHECK --interval=5s --retries=12 \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
