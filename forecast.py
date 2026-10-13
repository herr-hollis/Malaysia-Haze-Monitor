#!/usr/bin/env python3
"""
Experimental API forecast (next ~5 days) for every DOE station.

Method (simple on purpose):
  1. Daily mean of the DOE API per station (data/api_hourly.csv).
  2. Daily mean of Open-Meteo's modelled PM2.5 (CAMS) at the station coordinates (past + 5 forecast days).
  3. Per station: linear fit  API ~ a + b * PM2.5  on the days where both exist.
     Stations with fewer than MIN_DAYS overlapping days use one pooled fit over all stations.
  4. Apply the fit to the forecast days. 80% band = +/- 1.2816 * residual std; chance of API > 100 from the normal CDF.

Writes data/forecast.csv. Standard library only (Python 3.8+).
Run after collect.py:  python forecast.py
"""
import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

OUT = "data"
MYT = timezone(timedelta(hours=8))
URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
HEADERS = {"User-Agent": "haze-dashboard-collector/1.0 (student project)", "Accept-Encoding": "identity"}
MIN_DAYS = 5      # minimum overlapping days for a per-station fit
MIN_HOURS = 12    # hours needed for a DOE day to count
BATCH = 25        # coordinates per Open-Meteo request
Z80 = 1.2816


def fetch(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except Exception as exc:
            print(f"retry {attempt + 1}: {exc}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    return None


def open_meteo(stations):
    """Return {station_id: {date: mean pm2.5}} for past 30 + next 5 days."""
    result = {}
    for i in range(0, len(stations), BATCH):
        chunk = stations[i:i + BATCH]
        q = urllib.parse.urlencode({
            "latitude": ",".join(str(s["LATITUDE"]) for s in chunk),
            "longitude": ",".join(str(s["LONGITUDE"]) for s in chunk),
            "hourly": "pm2_5", "past_days": 30, "forecast_days": 5,
            "timezone": "Asia/Kuala_Lumpur",
        })
        data = fetch(URL + "?" + q)
        if data is None:
            continue
        if isinstance(data, dict):
            data = [data]
        for s, d in zip(chunk, data):
            h = d.get("hourly", {})
            by_day = defaultdict(list)
            for t, v in zip(h.get("time", []), h.get("pm2_5", [])):
                if v is not None:
                    by_day[t[:10]].append(v)
            result[s["STATION_ID"]] = {k: sum(v) / len(v) for k, v in by_day.items() if len(v) >= 12}
        time.sleep(1)
    return result


def daily_api(path):
    by = defaultdict(lambda: defaultdict(list))
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                by[r["STATION_ID"]][r["DATETIME"][:10]].append(float(r["API"]))
            except ValueError:
                pass
    return {s: {d: sum(v) / len(v) for d, v in days.items() if len(v) >= MIN_HOURS} for s, days in by.items()}


def fit(pairs):
    """Least squares y = a + b x. Returns (a, b, resid_std, r2)."""
    n = len(pairs)
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    b = sxy / sxx if sxx > 1e-9 else 0.0
    a = my - b * mx
    sse = sum((y - (a + b * x)) ** 2 for x, y in pairs)
    sst = sum((y - my) ** 2 for _, y in pairs)
    dof = max(n - 2, 1)
    return a, b, math.sqrt(sse / dof), (1 - sse / sst) if sst > 1e-9 else 0.0


def cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def build(stations, api, pm, today):
    pooled = [(pm[s][d], v) for s in api if s in pm for d, v in api[s].items() if d in pm[s] and d < today]
    pooled_fit = fit(pooled) if len(pooled) >= 3 else None
    rows = []
    for s in stations:
        sid = s["STATION_ID"]
        if sid not in pm:
            continue
        pairs = [(pm[sid][d], v) for d, v in api.get(sid, {}).items() if d in pm[sid] and d < today]
        if len(pairs) >= MIN_DAYS:
            f, method = fit(pairs), "station"
        elif pooled_fit:
            f, method = pooled_fit, "pooled"
        else:
            continue
        a, b, sd, r2 = f
        sd = max(sd, 8.0)  # never claim more certainty than +/-8 API
        n_fit = len(pairs)
        for d in sorted(pm[sid]):
            if d < today:
                continue
            pred = max(a + b * pm[sid][d], 0.0)
            rows.append({
                "STATION_ID": sid, "date": d,
                "api_pred": round(pred, 1),
                "api_low": round(max(pred - Z80 * sd, 0), 1),
                "api_high": round(pred + Z80 * sd, 1),
                "prob_over_100": round(1 - cdf((100 - pred) / sd), 3),
                "pm25_mean": round(pm[sid][d], 1),
                "n_fit": n_fit, "r2": round(r2, 2), "method": method,
            })
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "stations.csv"), newline="", encoding="utf-8") as f:
        stations = [r for r in csv.DictReader(f) if r["LATITUDE"] and r["LONGITUDE"]]
    api = daily_api(os.path.join(OUT, "api_hourly.csv"))
    pm = open_meteo(stations)
    if not pm:
        sys.exit("Open-Meteo failed; forecast not updated")
    today = datetime.now(MYT).strftime("%Y-%m-%d")
    rows = build(stations, api, pm, today)
    if not rows:
        sys.exit("no forecast rows; not updated")
    cols = ["STATION_ID", "date", "api_pred", "api_low", "api_high", "prob_over_100", "pm25_mean", "n_fit", "r2", "method"]
    with open(os.path.join(OUT, "forecast.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"forecast: {len(rows)} rows for {len({r['STATION_ID'] for r in rows})} stations")


if __name__ == "__main__":
    main()
