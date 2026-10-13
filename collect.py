#!/usr/bin/env python3
"""
Hourly collector for the DOE MyEQMS public API (Air Pollutant Index).

Each run:
  1. pulls the last ~25 hours of API for every state (apitablehourly),
  2. merges them into data/api_hourly.csv (no duplicates; newest value wins),
  3. refreshes data/stations.csv from the live station list (mapcaqm).

Run it every hour and the CSV grows into a real history, because the feed itself only shows ~25 hours.
Standard library only (Python 3.8+).
"""
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://eqms.doe.gov.my/api3/publicportalapims/"
OUT = "data"
MYT = timezone(timedelta(hours=8))
HEADERS = {"User-Agent": "haze-dashboard-collector/1.0 (student project)", "Accept": "application/json"}


def get(path, **params):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except Exception as exc:  # network error, bad JSON, HTTP error
            print(f"retry {attempt + 1} for {path}: {exc}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    # Usage: python collect.py            -> normal hourly run
    #        python collect.py 30         -> backfill: also fetch the previous 30 days (one window per day)
    back = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    now = datetime.now(MYT).replace(minute=0, second=0, microsecond=0)
    stamps = [(now - timedelta(days=d)).strftime("%Y-%m-%dT%H:00:00") for d in range(back + 1)]

    states = get("statelist")
    if not states:
        sys.exit("statelist failed; nothing saved")

    hourly_path = os.path.join(OUT, "api_hourly.csv")
    fields = ["STATION_ID", "STATE_ID", "DATETIME", "API"]
    merged = {}
    if os.path.exists(hourly_path):
        with open(hourly_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                merged[(row["STATION_ID"], row["DATETIME"])] = row
    before = len(merged)

    fetched = 0
    for state, stamp in ((st, sp) for st in states for sp in stamps):
        data = get("apitablehourly", stateid=state["STATE_ID"], datetime=stamp)
        rows = (data or {}).get("api_table_hourly", [])
        for r in rows:
            if r.get("API") is None:
                continue
            merged[(r["STATION_ID"], r["DATETIME"])] = {
                "STATION_ID": r["STATION_ID"],
                "STATE_ID": r["STATE_ID"],
                "DATETIME": r["DATETIME"],
                "API": r["API"],
            }
            fetched += 1
        time.sleep(1)  # be polite to the DOE server

    if fetched == 0:
        sys.exit("no rows fetched; nothing saved")

    with open(hourly_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for key in sorted(merged, key=lambda k: (k[1], k[0])):
            writer.writerow(merged[key])
    print(f"hourly: fetched {fetched} rows, +{len(merged) - before} new, {len(merged)} total")

    stations = get("mapcaqm", _ts=int(time.time() * 1000))
    rows = (stations or {}).get("data", [])
    if rows:
        cols = ["STATION_ID", "STATION_LOCATION", "STATE_ID", "STATE_NAME", "LATITUDE", "LONGITUDE"]
        with open(os.path.join(OUT, "stations.csv"), "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"stations: {len(rows)} saved")


if __name__ == "__main__":
    main()
