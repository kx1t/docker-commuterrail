# Copyright 2026 by Ramon F. Kolb, kx1t
#
# This file is part of docker-commuterrail.
#
# docker-commuterrail is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# docker-commuterrail is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with docker-commuterrail. If not, see <https://www.gnu.org/licenses/>.

FROM debian:trixie-slim AS fonts

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Self-host the Google Fonts used by index.html so the browser never needs to
# open connections to fonts.googleapis.com/fonts.gstatic.com at page load.
# This runs in its own build stage so curl/ca-certificates aren't needed in
# the runtime image, and the layer stays cached across app code changes.
RUN set -eux; \
    mkdir -p /app/fonts; \
    curl -fsSL --retry 3 -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36" \
      "https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Space+Grotesk:wght@400;500;600;700&display=swap" \
      -o /tmp/fonts.css; \
    grep -oE 'https://fonts\.gstatic\.com/[^)]+\.woff2' /tmp/fonts.css | sort -u | while read -r url; do \
      curl -fsSL --retry 3 "$url" -o "/app/fonts/$(basename "$url")"; \
    done; \
    sed -E 's#https://fonts\.gstatic\.com/[^)]+/([A-Za-z0-9_-]+\.woff2)#fonts/\1#g' /tmp/fonts.css > /app/fonts.css

FROM debian:trixie-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=80 \
    CACHE_TTL_SECONDS=60 \
    Cache-Time-Boston-Schedule=1800 \
    Cache-Time-Boston-Prediction=60 \
    Cache-Time-Boston-Vehicle=60 \
    Cache-Time-Boston-Alert=120 \
    Cache-Time-Boston-Stop=43200 \
    Cache-Time-Boston-Route=43200 \
    Cache-Time-Boston-Trip=43200 \
    Cache-Time-Paris-Default=60 \
    HTTP_WORKERS=10 \
    PRIM_API_KEY=""

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

COPY --from=fonts /app/fonts /app/fonts
COPY --from=fonts /app/fonts.css /app/fonts.css

COPY app/server.py /app/app/server.py
COPY app/map_snapshot.py /app/app/map_snapshot.py
COPY index.html /app/index.html

ARG GIT_COMMIT=unknown
ARG BUILD_DATE=unknown

ENV GIT_COMMIT=${GIT_COMMIT} \
    BUILD_DATE=${BUILD_DATE}

EXPOSE 80

CMD ["python3", "/app/app/server.py"]
