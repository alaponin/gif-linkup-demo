# KP2 demonstration console -- demo only, never production (see
# docs/production-delta.md). Mirrors apps/mock-registry's Dockerfile pattern.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY truth.py xroad.py journal.py app.py .
COPY static ./static
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
