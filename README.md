# transpo

Real-time departure board for Berlin public transit, designed for a monochrome e-ink display.

![Seeed Studio reTerminal E10-1](https://files.seeedstudio.com/wiki/reTerminalE10-1/reTerminalE10-1.webp)

Runs on a [Seeed Studio reTerminal E10-1](https://www.seeedstudio.com/reTerminal-E1001-p-6534.html) (7.5" 800×480 e-ink), refreshing every 5 minutes.

## Features

- **Departure times** — shows next catchable trips from nearby stops with "leave by" times so you know when to walk out the door
- **Frequent lines** — trams and buses that run every few minutes are grouped separately ("every Xm") instead of cluttering the board
- **Weather** — current conditions + 12-hour forecast (every 2 hours) in the header
- **Resilient** — background fetching every 2 minutes with caching; if the VBB API goes down, shows last known data with a staleness warning
- **Monochrome optimized** — black and white layout, no images, designed for e-ink contrast

## How it works

A Flask app fetches real-time departures from the [VBB transport API](https://v6.vbb.transport.rest) and weather from [Open-Meteo](https://open-meteo.com/). Data is fetched in background threads and cached, so page loads are instant (~30ms).

The board is split into two sections:

1. **Individual departures** — trips you need to plan for, sorted by when you need to leave home
2. **Frequent lines** — services running every ≤12 minutes per direction, shown as "every Xm"

Walk times to each stop are configured, and the "leave by" time accounts for both the walk and any delays.

## Configuration

Edit the constants at the top of `app.py`:

- `STOPS` — list of `(stop_id, name, walk_minutes, duration, [results])` tuples
- `OVERLAPPING` — lines to skip at a stop because they're better covered by a closer stop
- `SKIP_PRODUCTS` — product types (Tram, Bus, S, etc.) to skip at a stop
- `LAT, LON` — coordinates for weather

Stop IDs can be found at [v6.vbb.transport.rest](https://v6.vbb.transport.rest).

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

The board is served at `http://localhost:8090`.

A systemd unit file (`transpo.service`) is included for running as a service.

## APIs

- [VBB transport.rest v6](https://v6.vbb.transport.rest) — real-time Berlin/Brandenburg transit departures (no API key needed)
- [Open-Meteo](https://open-meteo.com/) — weather forecast (free, no API key needed)
