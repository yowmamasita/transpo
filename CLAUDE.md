# transpo

E-ink departure board for Berlin transit. Runs on a Seeed Studio reTerminal E10-1 (7.5" 800×480 monochrome). Screen refreshes every 5 minutes.

## Architecture

Single-file Flask app (`app.py`) + one Jinja2 template (`templates/index.html`). No database.

- **Background threads** fetch data and cache it; page loads serve from cache (~30ms)
- Departures fetched every 2 min (`FETCH_INTERVAL`), weather every 10 min (`WEATHER_INTERVAL`)
- Departures come first on startup so the page is never empty on first load

## Display Layout (800×480)

- **Header row**: title, current weather (icon + temp + feels-like + description + wind), 6-slot forecast (every 2h, 12h ahead), clock
- **Individual departures**: trips you need to plan for, sorted by "leave by" time. Only shows catchable trips (leave_in >= 0)
- **Frequent section**: lines running every ≤12 min per direction, collapsed to "every Xm". Separator bar: "FREQUENT — just walk, next one is coming"
- Row count is dynamic: `max_rows = 13 - frequent_count - 1`

## Key Design Decisions

- **"Leave by" not "departs at"**: the board answers "when do I leave the house?" — the departure time at the stop is not shown
- **Frequent grouping**: calculated per line+stop+direction to get accurate intervals (mixing directions gives wrong averages). Lines appearing in frequent are excluded from individual rows to avoid duplicates
- **OVERLAPPING filter**: lines reachable from a closer stop are skipped at farther stops (e.g. S9 skipped at Schöneweide because Johannisthal is closer)
- **SKIP_PRODUCTS**: entire product types filtered at a stop (trams skipped at Johannisthal since user won't ride there — they show at Schöneweide instead)
- **Schöneweide special config**: 90-min duration + 50 results (vs 60min/20 for others) because the 20-min walk means more results needed to find catchable trips
- **No delay column shown**: delay is already factored into the "leave by" time
- **Weather forecast skips current hour** (duplicate of header) and shows every 2 hours

## Deployment

Runs on zen (192.168.88.244:8090) as systemd service `transpo.service`.

Accessible via:
- `http://eink2.sarmiento.cc:8090` — direct port forward (fastest)
- `https://eink2.sarmiento.cc` — via Nginx Proxy Manager on fun, SSL with Let's Encrypt cert (id 82)
- `http://eink2.sarmiento.cc` — redirects to HTTPS

## APIs (no keys needed)

- [VBB transport.rest v6](https://v6.vbb.transport.rest) — Berlin/Brandenburg real-time departures
- [Open-Meteo](https://open-meteo.com/) — weather forecast

## Stop IDs

Find at https://v6.vbb.transport.rest/locations?query=STOP_NAME
