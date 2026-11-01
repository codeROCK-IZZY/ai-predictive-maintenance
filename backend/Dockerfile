# One image, two roles: API (default CMD) and worker (`python -m app.cli worker`).
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# No apt packages needed: XGBoost wheels bundle their OpenMP runtime, and the
# health check uses Python instead of curl (smaller image, fewer CVEs to patch).
WORKDIR /app
COPY requirements.txt .
# Optional build secret `extra_ca` for networks with a TLS-inspecting proxy:
#   docker build --secret id=extra_ca,src=/path/to/ca.crt ...   (not stored in the image)
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -r requirements.txt
COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY app ./app
# Run as an unprivileged user; only /app/var (artifacts, uploads) is writable.
RUN useradd --uid 10001 --create-home pdm && mkdir -p /app/var && chown -R pdm /app/var
USER pdm
ENV PDM_ARTIFACT_DIR=/app/var/artifacts PDM_UPLOAD_DIR=/app/var/uploads
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health/live', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "2"]
