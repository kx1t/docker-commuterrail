FROM debian:trixie-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=80 \
    CACHE_TTL_SECONDS=60 \
    HTTP_WORKERS=10 \
    PRIM_API_KEY=""

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

COPY app /app/app
COPY index.html /app/index.html
COPY README.md /app/README.md

EXPOSE 80

CMD ["python3", "/app/app/server.py"]
