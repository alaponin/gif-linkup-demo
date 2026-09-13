# The film profile's browser (docker-compose.yml `capture`). Linux Chromium
# at a pinned digest renders the same pixels run to run and machine to
# machine, which a host browser cannot promise. The base image carries the
# browsers and their system libraries but not the Python package, so the
# package is pinned here to the same Playwright release.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble@sha256:aa81288e738725378becba5b3e06cb0f3a7f012a610e87e8d767a090ea3f740d
RUN pip install --no-cache-dir --break-system-packages playwright==1.62.0 PyYAML==6.0.3
WORKDIR /capture
COPY capture.py beats.yaml ./
ENTRYPOINT ["python3", "/capture/capture.py"]
