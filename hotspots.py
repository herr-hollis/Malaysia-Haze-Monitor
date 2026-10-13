#!/usr/bin/env python3
"""
Fetch NASA FIRMS VIIRS hotspots for Southeast Asia, label each with a land region
(Sumatra, Kalimantan, Peninsular Malaysia, Sabah/Sarawak, Thailand, Brunei, Singapore, Offshore)
using regions.geojson, and merge into data/hotspots.csv (deduplicated).

Needs:  nothing beyond Python (pure-Python point-in-polygon).
Key:    set environment variable FIRMS_KEY (never put the key in a file you publish).
Usage:  python hotspots.py        # last 2 days
        python hotspots.py 5      # last 5 days (FIRMS allows 1-5 per call)
"""
import csv, io, json, os, sys, urllib.request

KEY = os.environ.get("FIRMS_KEY")
if not KEY:
    sys.exit("Set FIRMS_KEY first")
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 2
URL = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{KEY}/VIIRS_SNPP_NRT/95,-6,120,8/{DAYS}"

here = os.path.dirname(os.path.abspath(__file__))
def _in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside

def _in_polygon(x, y, poly):  # poly = [outer, hole, hole, ...]
    return _in_ring(x, y, poly[0]) and not any(_in_ring(x, y, h) for h in poly[1:])

regions = []
for f in json.load(open(os.path.join(here, "regions.geojson")))["features"]:
    g = f["geometry"]
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    for p in polys:
        xs = [c[0] for c in p[0]]; ys = [c[1] for c in p[0]]
        regions.append((f["properties"]["region"], (min(xs), min(ys), max(xs), max(ys)), p))

def region_of(lat, lon):
    for name, (x0, y0, x1, y1), poly in regions:
        if x0 <= lon <= x1 and y0 <= lat <= y1 and _in_polygon(lon, lat, poly):
            return name
    return "Offshore"

req = urllib.request.Request(URL, headers={"User-Agent": "haze-dashboard/1.0"})
text = urllib.request.urlopen(req, timeout=120).read().decode("utf-8")
if not text.startswith("latitude"):
    sys.exit("Unexpected FIRMS reply: " + text[:200])

fields = ["latitude", "longitude", "acq_date", "acq_time", "confidence", "frp", "daynight", "region"]
path = os.path.join(here, "data", "hotspots.csv")
os.makedirs(os.path.dirname(path), exist_ok=True)
merged = {}
if os.path.exists(path):
    for r in csv.DictReader(open(path, newline="", encoding="utf-8")):
        merged[(r["latitude"], r["longitude"], r["acq_date"], r["acq_time"])] = r
before = len(merged)
for r in csv.DictReader(io.StringIO(text)):
    k = (r["latitude"], r["longitude"], r["acq_date"], r["acq_time"])
    merged[k] = {f: r.get(f, "") for f in fields[:-1]} | {"region": region_of(float(r["latitude"]), float(r["longitude"]))}
with open(path, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(sorted(merged.values(), key=lambda r: (r["acq_date"], r["acq_time"])))
print(f"hotspots: +{len(merged) - before} new, {len(merged)} total")
