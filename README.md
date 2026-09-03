# Fitchburg Line Live Departures

This repository contains the front-end dashboard and a small Dockerized transit cache service for both Paris PRIM and Boston MBTA data. The cache runs on a single port, stores responses in memory, and refreshes data only after a browser request for a specific transit authority within the last 60 seconds. This keeps quota usage low and avoids unnecessary upstream refreshes when no browser is actively using the service.

## Containerized cache architecture

The page should call the local cache service instead of PRIM or MBTA directly. For each transit authority, the cache keeps per-authority request timing so the app can go stale when no browser is actively asking for that transit data. If a browser request arrives while the cache is missing or stale, the server refreshes it immediately. If the refresh fails, the browser receives a warning or error message instead of silently failing.

### Runtime env vars

The cache container uses Docker environment variables instead of a JSON file. Set the upstream keys using environment variables such as:

This is the MBTA-side implementation of the caching scheme: the server records the last request time per transit authority, only refreshes when a recent browser request exists, and serves stale data with a warning if refreshing fails.

### Docker Compose example

```yaml
services:
  commuterrail:
    image: ghcr.io/kx1t/docker-commuterrail:latest
    container_name: commuterrail
    restart: unless-stopped
    environment:
      PORT: "80"
      DATA_DIR: "/data"
      CACHE_TTL_SECONDS: "60"
      HTTP_WORKERS: "10"
      PRIM_API_KEY: "${PRIM_API_KEY}"
      MBTA_API_KEY: "${MBTA_API_KEY}"
    ports:
      - "8080:80"
    volumes:
      - ./commuterrail:/data
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=64m
      - /run:rw,noexec,nosuid,size=32m
    read_only: true
```

This exposes port 80 inside the container, leaves the host port flexible via Docker port mapping, and persists the cache statistics journal to a local `./commuterrail` directory on disk so it survives container restarts. The runtime still keeps all non-persistent runtime files in tmpfs, matching the requirement that cached values and ephemeral runtime directories should not be on disk.

### Cache statistics and diagnostics

The app exposes a mobile-friendly diagnostics view via the footer link `Data from ...`. It reads the latest cache and client statistics from the `/api/stats` endpoint and shows per-transit cache activity, recent hit/miss/refresh counts over the last minute, hour, and previous 24 hours, plus observed client browser IDs and sanitized source IP provenance with private-range addresses suppressed.

### Reverse proxy layout

The container is designed to sit behind a reverse proxy. The app itself does not need TLS. The web UI should keep the same single-endpoint pattern with a `transit` GET parameter, but instead of talking straight to upstream APIs it pings the local cache service.

Example browser request:

```text
https://example.com/commuterrail/?transit=paris
```

The reverse proxy can route that to the cache service or to a static frontend that uses the cache service as a backend.

## Front-end behavior

The page still supports the single endpoint with `transit` selection. For Paris and Boston, the frontend calls the cache endpoint rather than hitting the upstream APIs directly. The cache maintains a 60-second TTL and serves stale data only if the server cannot refresh it after a recent request; the browser then shows an inline warning or error message.

## Error handling contract

The cache server emits browser-visible messages in these cases:

- stale data + refresh fails: `Warning: Transit data was last updated on date/time. Server error ###: message`
- missing cache + refresh fails: `Error: transit data cannot be retrieved. Server error ###: message`
- any other errors: a short and descriptive error text

## Docker build and deployment

The image is intended to be published as a multi-arch image to GHCR on each push to `main`:

```sh
docker buildx build --platform linux/amd64,linux/arm64 --push -t ghcr.io/kx1t/docker-commuterrail:latest .
```

The repo includes a GitHub Action that performs this build automatically, with each architecture built in parallel and Docker layer caches shared via GitHub Actions cache.

## Notes

- No JSON secret file is used. All secret configuration comes from Docker environment variables.
- Cache values are in-memory only and should live in tmpfs-backed runtime memory.
- The service is stateless, simple, and suitable for container deployment behind nginx or another reverse proxy.
