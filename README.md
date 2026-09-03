# Fitchburg Line Live Departures

A single-file MBTA Commuter Rail dashboard. CSS, JavaScript, install metadata, and the favicon are all embedded in `index.html`; it needs no build step and can be served directly by nginx.

## Deploy with nginx

Copy the contents of this folder to the web root configured for your site:

```sh
sudo mkdir -p /var/www/fitchburg-rail
sudo install -m 0644 index.html /var/www/fitchburg-rail/index.html
sudo install -m 0644 runtime-config.php /var/www/fitchburg-rail/runtime-config.php
```

Deploy to your current production target (copies both files, then installs with root ownership and correct permissions):

```sh
scp index.html runtime-config.php pi@webproxy:/tmp/ && \
ssh pi@webproxy 'sudo install -o root -g root -m 0644 /tmp/index.html /opt/webproxy/webproxy/html/commuterrail/index.html && sudo install -o root -g root -m 0644 /tmp/runtime-config.php /opt/webproxy/webproxy/html/commuterrail/runtime-config.php && rm -f /tmp/index.html /tmp/runtime-config.php'
```

Example server block:

```nginx
server {
    listen 80;
    server_name rail.example.com;
    root /var/www/fitchburg-rail;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

The page calls the public MBTA v3 API from the browser. For Boston, pass `boston_api_key=YOUR_KEY`; the older `api_key=YOUR_KEY` parameter remains supported as an alias. The optional `transit` parameter selects the provider: omit it or use `?transit=boston` for MBTA, or use `?transit=paris` for RATP/IDFM. The value is case-insensitive. For Paris, pass `paris_api_key=YOUR_KEY`. Paris uses the official PRIM `estimated-timetable` endpoint and sends the client-side token as the documented `apikey` header. Tap the current line name to choose another line, endpoint, and remote station. The selected values are saved separately for each provider in browser storage. Boston defaults to the existing Fitchburg/Belmont/North Station setup; Paris defaults to Metro line 9, Saint-Ambroise, and Pont de Sèvres. The page refreshes every 60 seconds and can also be refreshed manually.

## Runtime API key loading (no key in URL)

The page now tries to load keys from same-origin runtime config files when URL parameters are not provided:

1. `runtime-config.json` (static hosting friendly)
1. `runtime-config.php` (if server-side PHP is enabled)

### Option A: static JSON file (works on static-only hosting)

1. Copy `runtime-config.json.example` to `/opt/webproxy/secrets/runtime-config.json` on the server.
1. Fill in real keys and do not commit this file.
1. If using `runtime-config.php`, this path is the default location it reads.

### Option B: PHP endpoint backed by non-web file

1. Deploy `runtime-config.php` in the same directory as `index.html`.
1. Ensure your web server executes PHP (for example via php-fpm). If PHP is not enabled, this file will be served as text and will not work as an endpoint.
1. Create a non-web-accessible file on the server at:
    - `/opt/webproxy/secrets/commuterrail-config.php`
1. Use this file format (copy from `commuterrail-config.php.example`):

```php
<?php

return [
    'boston_api_key' => 'replace-with-boston-key',
    'paris_api_key' => 'replace-with-paris-key',
];
```

1. Lock down permissions so only the web server user can read that file.

Notes:

- This removes keys from the page URL and browser history.
- Advanced users can still inspect network traffic and view the returned token values.
- Keep quotas/rate limits conservative and rotate keys when needed.

For an iPhone shortcut, open the deployed HTTPS URL in Safari, tap Share, then Add to Home Screen. HTTPS is recommended so the manifest and live API requests work reliably.
