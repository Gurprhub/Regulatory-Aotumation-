# syntax=docker/dockerfile:1

# Build stage: compile dependencies into wheels so the runtime image carries no
# compiler or build headers.
FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


# Runtime stage.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# The app never needs to write to its own code, and nothing here runs as root.
RUN useradd --create-home --uid 10001 app

WORKDIR /app

COPY --from=build /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY app ./app
COPY scripts ./scripts

USER app

EXPOSE 8000

# The probe hits the one endpoint that needs no credentials.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# import_circle is a no-op once the archive is loaded, so this stays cheap on
# every restart; pass --replace by hand after refreshing circle.html.
# --proxy-headers and --forwarded-allow-ips matter behind a TLS-terminating
# load balancer: without them the app sees every request as plain HTTP from the
# proxy, and secure-cookie and redirect behaviour goes wrong.
CMD ["sh", "-c", "python -m scripts.init_db && python -m scripts.import_circle && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-4} --proxy-headers --forwarded-allow-ips='*'"]
