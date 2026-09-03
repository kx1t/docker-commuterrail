# Fitchburg Line Live Departures

This repository contains the front-end dashboard and a small Dockerized cache service for Paris PRIM data. The cache service runs on a single port and keeps a 60-second in-memory cache so the browser does not call the upstream API directly on every refresh.

## Containerized cache architecture

The page should call the local cache service instead of PRIM directly. The cache service fetches live Paris data once per minute, caches it in memory, and serves that to the browser. This reduces quota consumption and avoids repeated browser requests from causing upstream 429s.

### Runtime env vars

The cache container uses Docker environment variables instead of a JSON file. Set the API key using environment variables such as:

```sh
PRIM_API_KEY=your-prim-key
CACHE_TTL_SECONDS=60
PORT=80
HTTP_WORKERS=10
```

### Docker Compose example

```yaml
services:
  commuterrail-cache:
    image: ghcr.io/kx1t/docker-commuterrail:latest
    container_name: commuterrail-cache
    restart: unless-stopped
    environment:
      PORT: "80"
      CACHE_TTL_SECONDS: "60"
      HTTP_WORKERS: "10"
      PRIM_API_KEY: "${PRIM_API_KEY}"
    ports:
      - "80:80"
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=64m
      - /run:rw,noexec,nosuid,size=32m
    read_only: true
```

This exposes port 80 on the host and keeps all non-persistent runtime files in tmpfs, matching the requirement that cached values and ephemeral runtime directories should not be on disk.

### Reverse proxy layout

The container is designed to sit behind a reverse proxy. The app itself does not need TLS. The web UI should keep the same single-endpoint pattern with a `transit` GET parameter, but instead of talking straight to PRIM it pings the cache service.

Example browser request:

```text
https://example.com/commuterrail/?transit=paris
```

The reverse proxy can route that to the cache service or to a static frontend that uses the cache service as a backend.

## Front-end behavior

The page still supports the single endpoint with `transit` selection. For Paris, the frontend should call the cache service endpoint rather than the upstream PRIM API directly. The cache service maintains a 60-second TTL and serves a response until the next fetch window.

## Docker build and deployment

The image is intended to be published as a multi-arch image to GHCR on each push to `main`:

```sh
docker buildx build --platform linux/amd64,linux/arm64 --push -t ghcr.io/kx1t/docker-commuterrail:latest .
```

The repo includes a GitHub Action that performs this build automatically.

## Notes

- No JSON secret file is used. All secret configuration comes from Docker environment variables.
- Cache values are in-memory only and should live in tmpfs-backed runtime memory.
- This design keeps the service stateless, simple, and suitable for container deployment behind nginx or another reverse proxy.
