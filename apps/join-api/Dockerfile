# KP2 member-join API -- demo only, never production (see
# docs/production-delta.md). Mirrors apps/console's Dockerfile pattern.
# Base image digest-pinned (C13), resolved 2026-08-01 -- same digest as
# apps/console. Unlike the X-Road images, a pinned base stops receiving
# security patches until someone re-pins it -- re-pin periodically, not
# just when X-Road bumps.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY app.py schema.py validate.py writer.py .
USER nobody
EXPOSE 8000
HEALTHCHECK --interval=5s --retries=12 \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
