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

COPY README.md /app/README.md
COPY app/server.py /app/app/server.py
COPY index.html /app/index.html

ARG GIT_COMMIT=unknown
ARG BUILD_DATE=unknown

ENV GIT_COMMIT=${GIT_COMMIT} \
    BUILD_DATE=${BUILD_DATE}

EXPOSE 80

CMD ["python3", "/app/app/server.py"]
