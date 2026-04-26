from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Thread, Lock
from math import radians, sin, cos, sqrt, atan2
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, render_template, request
from zoneinfo import ZoneInfo

app = Flask(__name__)

VBB_BASE = "https://v6.vbb.transport.rest"
BERLIN = ZoneInfo("Europe/Berlin")
FETCH_INTERVAL = 120
WEATHER_INTERVAL = 600

DURATION_MINUTES = 60
LAT, LON = 52.4496, 13.5263

STOPS = [
    ("900192503", "Rudower/Schnellerstr.", 12, DURATION_MINUTES),
    ("900192002", "S Oberspree", 18, DURATION_MINUTES),
    ("900193001", "S Johannisthal", 18, DURATION_MINUTES),
    ("900192001", "S Schöneweide", 20, 90, 50),
]

OVERLAPPING = {
    "900192002": {"165"},
    "900193001": {"M17"},
    "900192001": {"165", "S46", "S85", "S9", "M11", "166", "160", "265"},
}

SKIP_PRODUCTS = {
    "900193001": {"Tram"},
}

PRODUCT_ICONS = {
    "S": "🚈",
    "Bus": "🚌",
    "Tram": "🚊",
    "U": "🚇",
    "RE": "🚆",
    "RB": "🚆",
}

WMO_ICONS = {
    0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌧️",
    56: "🌧️", 57: "🌧️",
    61: "🌦️", 63: "🌧️", 65: "🌧️",
    66: "🌧️", 67: "🌧️",
    71: "🌨️", 73: "🌨️", 75: "❄️",
    77: "❄️",
    80: "🌦️", 81: "🌧️", 82: "🌧️",
    85: "🌨️", 86: "🌨️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}

WMO_DESC = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Sleet",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Severe thunderstorm",
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))
session.headers.update({"Accept": "application/json", "User-Agent": "transpo-departure-board/1.0"})

WALK_SPEED_KMH = 4.5
LOCATION_CACHE_TTL = 120

_lock = Lock()
_cache = {"departures": [], "frequent": [], "alerts": [], "fetched_at": None, "stale": False, "weather": None}
_location_cache = {}
_location_cache_lock = Lock()


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371 * 2 * atan2(sqrt(a), sqrt(1 - a))


def _geocode_location(query):
    try:
        resp = session.get(
            f"{VBB_BASE}/locations",
            params={"query": query, "results": 1, "addresses": True, "poi": True, "stops": True},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            loc = results[0]
            lat, lon = loc.get("latitude"), loc.get("longitude")
            if lat and lon:
                return lat, lon, loc.get("name", query)
    except Exception:
        pass
    return None


def _find_nearby_stops(lat, lon, max_results=10):
    try:
        resp = session.get(
            f"{VBB_BASE}/stops/nearby",
            params={"latitude": lat, "longitude": lon, "results": max_results},
            timeout=10,
        )
        resp.raise_for_status()
        stops = resp.json()
    except Exception:
        return []

    result = []
    for stop in stops:
        sid = stop.get("id")
        name = stop.get("name", "?")
        slat = stop.get("location", {}).get("latitude")
        slon = stop.get("location", {}).get("longitude")
        if not (sid and slat and slon):
            continue
        dist_km = _haversine_km(lat, lon, slat, slon)
        walk_min = max(1, round(dist_km / WALK_SPEED_KMH * 60))
        result.append((sid, name, walk_min, DURATION_MINUTES))
    return result


def _fetch_for_location(location_query):
    now = time.monotonic()
    with _location_cache_lock:
        cached = _location_cache.get(location_query)
        if cached and now - cached["time"] < LOCATION_CACHE_TTL:
            return cached["data"]

    geo = _geocode_location(location_query)
    if not geo:
        return None

    lat, lon, name = geo
    stops = _find_nearby_stops(lat, lon)
    if not stops:
        return None

    all_deps = []
    all_alerts = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(fetch_departures, sid, sname, walk, dur)
            for sid, sname, walk, dur in stops
        ]
        for future in futures:
            try:
                deps, alerts = future.result()
            except Exception:
                continue
            if deps is not None:
                all_deps.extend(deps)
            all_alerts.extend(alerts)

    all_alerts = _dedupe_alerts(all_alerts)
    catchable, frequent = _process_departures(all_deps)
    weather = fetch_weather(lat, lon)

    data = {
        "departures": catchable,
        "frequent": frequent,
        "alerts": all_alerts,
        "weather": weather,
        "location_name": name,
        "lat": lat,
        "lon": lon,
    }

    with _location_cache_lock:
        _location_cache[location_query] = {"data": data, "time": now}

    return data


def fetch_departures(stop_id, stop_name, walk_minutes, duration, results=20):
    try:
        resp = session.get(
            f"{VBB_BASE}/stops/{stop_id}/departures",
            params={"duration": duration, "results": results, "remarks": "true"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None, []

    departures = []
    alerts = []
    seen_alerts = set()
    now = datetime.now(timezone.utc)
    skip_lines = OVERLAPPING.get(stop_id, set())
    skip_products = SKIP_PRODUCTS.get(stop_id, set())

    for dep in data.get("departures", []):
        when_str = dep.get("when") or dep.get("plannedWhen")
        if not when_str:
            continue

        when = datetime.fromisoformat(when_str)
        minutes_away = int((when - now).total_seconds() / 60)
        if minutes_away < 0:
            continue

        line = dep.get("line", {})
        line_name = line.get("name", "?")
        product = line.get("productName", "")
        direction = dep.get("direction", "")

        for remark in dep.get("remarks", []):
            if remark.get("type") == "warning" and remark.get("summary"):
                key = (line_name, remark["summary"])
                if key not in seen_alerts:
                    seen_alerts.add(key)
                    alerts.append({"line": line_name, "icon": PRODUCT_ICONS.get(product, ""), "summary": remark["summary"]})
            elif remark.get("code") == "EV":
                key = (line_name, "EV")
                if key not in seen_alerts:
                    seen_alerts.add(key)
                    alerts.append({"line": line_name, "icon": PRODUCT_ICONS.get(product, ""), "summary": "Replacement bus (Ersatzverkehr)"})

        if line_name in skip_lines:
            continue
        if product in skip_products:
            continue

        if len(direction) > 28:
            direction = direction[:26] + ".."

        delay = dep.get("delay")
        delay_min = (delay // 60) if delay and delay > 0 else 0

        leave_in = minutes_away - walk_minutes
        leave_at = (now + timedelta(minutes=leave_in)).astimezone(BERLIN).strftime("%H:%M")
        icon = PRODUCT_ICONS.get(product, "")
        departures.append({
            "line": line_name,
            "product": product,
            "icon": icon,
            "direction": direction,
            "minutes": minutes_away,
            "delay": delay_min,
            "stop": stop_name,
            "walk": walk_minutes,
            "leave_in": leave_in,
            "leave_at": leave_at,
            "time": when.astimezone(BERLIN).strftime("%H:%M"),
        })

    return departures, alerts


def _process_departures(all_deps):
    dir_groups = {}
    for d in all_deps:
        key = (d["line"], d["stop"], d["direction"])
        dir_groups.setdefault(key, []).append(d)

    line_freq = {}
    unique = []

    for (line_name, stop_name, direction), deps in dir_groups.items():
        deps_sorted = sorted(deps, key=lambda d: d["minutes"])
        intervals = [deps_sorted[i+1]["minutes"] - deps_sorted[i]["minutes"] for i in range(len(deps_sorted)-1)]
        avg_interval = max(1, round(sum(intervals) / len(intervals))) if intervals else 99
        if len(deps) >= 2 and avg_interval <= 12:
            lk = (line_name, stop_name)
            if lk not in line_freq:
                line_freq[lk] = {"line": line_name, "icon": deps_sorted[0]["icon"], "stop": stop_name, "walk": deps_sorted[0]["walk"], "intervals": [], "directions": []}
            line_freq[lk]["intervals"].append(avg_interval)
            line_freq[lk]["directions"].append(direction)
        else:
            unique.extend(deps)

    frequent = []
    for lk, info in line_freq.items():
        avg = max(1, round(sum(info["intervals"]) / len(info["intervals"])))
        dirs = list(dict.fromkeys(info["directions"]))
        frequent.append({
            "line": info["line"],
            "icon": info["icon"],
            "stop": info["stop"],
            "walk": info["walk"],
            "every_min": avg,
            "directions": dirs,
        })

    freq_keys = {(f["line"], f["stop"]) for f in frequent}
    catchable = [d for d in unique if d["leave_in"] >= 0 and (d["line"], d["stop"]) not in freq_keys]
    catchable.sort(key=lambda d: d["leave_in"])
    frequent.sort(key=lambda f: f["every_min"])
    return catchable, frequent


def fetch_weather(lat=LAT, lon=LON):
    try:
        resp = session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "hourly": "temperature_2m,weather_code,precipitation_probability",
                "timezone": "Europe/Berlin",
                "forecast_hours": 14,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    current = data.get("current", {})
    hourly = data.get("hourly", {})

    hours = []
    times = hourly.get("time", [])
    for i in range(len(times)):
        h = int(times[i][11:13])
        if i == 0:
            continue
        if h % 2 != 0:
            continue
        code_h = hourly["weather_code"][i]
        precip = hourly.get("precipitation_probability", [0] * len(times))
        hours.append({
            "time": times[i][11:16],
            "temp": round(hourly["temperature_2m"][i]),
            "icon": WMO_ICONS.get(code_h, "?"),
            "rain": precip[i] if i < len(precip) else 0,
        })
        if len(hours) >= 6:
            break

    code = current.get("weather_code", 0)
    return {
        "temp": round(current.get("temperature_2m", 0)),
        "feels": round(current.get("apparent_temperature", 0)),
        "wind": round(current.get("wind_speed_10m", 0)),
        "icon": WMO_ICONS.get(code, "?"),
        "desc": WMO_DESC.get(code, ""),
        "hours": hours,
    }


def _dedupe_alerts(alerts):
    seen = set()
    result = []
    for a in alerts:
        key = (a["line"], a["summary"])
        if key not in seen:
            seen.add(key)
            result.append(a)
    lines_with_warnings = {a["line"] for a in result if a["summary"] != "Replacement bus (Ersatzverkehr)"}
    return [a for a in result if a["summary"] != "Replacement bus (Ersatzverkehr)" or a["line"] not in lines_with_warnings]


def _do_fetch():
    all_deps = []
    all_alerts = []
    any_success = False

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(fetch_departures, sid, name, walk, dur, *extra)
            for sid, name, walk, dur, *extra in STOPS
        ]
        for future in futures:
            try:
                deps, alerts = future.result()
            except Exception:
                continue
            if deps is not None:
                any_success = True
                all_deps.extend(deps)
            all_alerts.extend(alerts)

    if any_success:
        catchable, frequent = _process_departures(all_deps)
        all_alerts = _dedupe_alerts(all_alerts)
        with _lock:
            _cache["departures"] = catchable
            _cache["frequent"] = frequent
            _cache["alerts"] = all_alerts
            _cache["fetched_at"] = datetime.now(BERLIN)
            _cache["stale"] = False
    else:
        with _lock:
            _cache["stale"] = True


def _do_weather():
    weather = fetch_weather()
    if weather:
        with _lock:
            _cache["weather"] = weather


def _background_fetcher():
    _do_fetch()
    _do_weather()
    weather_tick = 0
    while True:
        time.sleep(FETCH_INTERVAL)
        _do_fetch()
        weather_tick += FETCH_INTERVAL
        if weather_tick >= WEATHER_INTERVAL:
            _do_weather()
            weather_tick = 0


@app.route("/")
def index():
    location_query = request.args.get("location")

    if location_query:
        data = _fetch_for_location(location_query)
        if data:
            now = datetime.now(BERLIN).strftime("%H:%M")
            return render_template("index.html", departures=data["departures"], frequent=data["frequent"],
                                   alerts=data.get("alerts", []),
                                   now=now, stale_minutes=None, weather=data["weather"],
                                   location_name=data["location_name"])

    with _lock:
        departures = list(_cache["departures"])
        frequent = list(_cache["frequent"])
        alerts = list(_cache["alerts"])
        fetched_at = _cache["fetched_at"]
        stale = _cache["stale"]
        weather = _cache["weather"]

    stale_minutes = None
    if stale and fetched_at:
        age = datetime.now(BERLIN) - fetched_at
        stale_minutes = int(age.total_seconds() / 60)

    now = datetime.now(BERLIN).strftime("%H:%M")
    return render_template("index.html", departures=departures, frequent=frequent, alerts=alerts, now=now, stale_minutes=stale_minutes, weather=weather)


_fetcher = Thread(target=_background_fetcher, daemon=True)
_fetcher.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090)
