"""
Build app/static/geo.json, the vector basemap for the dashboard.

Source: Natural Earth 1:50m (public domain) via the `sane-topojson` npm package:
    npm pack sane-topojson && tar -xzf sane-topojson-*.tgz
    python scripts/build_geo.py package/dist/asia_50m.json

Keeps only what the KKH map needs: the Indus river, the Pakistan-China and Pakistan-Afghanistan
boundary lines in the far north, plus hand-checked peaks and towns. Segment routes along the
Indus (Besham -> Raikot) are snapped to the river's real course so the road follows the gorge.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kkh.config import SEGMENTS  # noqa: E402

BBOX = (72.45, 33.5, 76.0, 37.15)          # west, south, east, north
OUT = ROOT / "app" / "static" / "geo.json"

PEAKS = [("Nanga Parbat", 35.2375, 74.5892, 8126), ("Rakaposhi", 36.1425, 74.4897, 7788),
         ("Batura Sar", 36.5106, 74.5222, 7795), ("Haramosh", 35.8406, 74.8975, 7409),
         ("Ultar Sar", 36.3986, 74.7203, 7388), ("Diran", 36.1214, 74.6614, 7266)]
TOWNS = [("Islamabad", 33.684, 73.048, 1), ("Abbottabad", 34.149, 73.199, 2), ("Mansehra", 34.333, 73.201, 3),
         ("Besham", 34.932, 72.873, 2), ("Dasu", 35.292, 73.290, 2), ("Chilas", 35.421, 74.094, 2),
         ("Gilgit", 35.920, 74.308, 1), ("Skardu", 35.297, 75.633, 2), ("Karimabad", 36.322, 74.664, 2),
         ("Passu", 36.468, 74.895, 3), ("Sost", 36.690, 74.820, 2), ("Khunjerab Pass", 36.850, 75.427, 1),
         ("Jaglot", 35.690, 74.620, 3), ("Thakot", 34.861, 72.918, 3)]
ATTABAD_LAKE = [[74.810, 36.318], [74.828, 36.322], [74.846, 36.331], [74.858, 36.345], [74.864, 36.362],
                [74.866, 36.378]]   # approximate centreline of the 2010 landslide lake


def decode(topo):
    tr = topo.get("transform")
    arcs = []
    for a in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in a:
            if tr:
                x += dx; y += dy
                pts.append((x * tr["scale"][0] + tr["translate"][0], y * tr["scale"][1] + tr["translate"][1]))
            else:
                pts.append((dx, dy))
        arcs.append(pts)
    return arcs


def inb(p, pad=0.0):
    return BBOX[0] - pad <= p[0] <= BBOX[2] + pad and BBOX[1] - pad <= p[1] <= BBOX[3] + pad


def clip(line):
    out, cur = [], []
    for p in line:
        if inb(p, 0.15):
            cur.append([round(p[0], 4), round(p[1], 4)])
        elif cur:
            out.append(cur); cur = []
    if len(cur) > 1:
        out.append(cur)
    return [c for c in out if len(c) > 1]


def lines_of(topo, layer):
    arcs = decode(topo)
    arc = lambda i: arcs[i] if i >= 0 else arcs[~i][::-1]
    res = []
    for g in topo["objects"][layer]["geometries"]:
        A = g.get("arcs", [])
        t = g["type"]
        groups = ([A] if t == "LineString" else A if t in ("MultiLineString", "Polygon")
                  else [r for p in A for r in p] if t == "MultiPolygon" else [])
        for grp in groups:
            pts = []
            for i in grp:
                a = arc(i)
                pts += a if not pts else a[1:]
            res.append(pts)
    return res


def snap_routes(indus: list[list[float]]):
    """Replace the coarse vertices of Indus-side segments with the river's own course."""
    pts = [p for line in indus for p in line]

    def nearest(lat, lon):
        return min(range(len(pts)), key=lambda i: (pts[i][0] - lon) ** 2 + ((pts[i][1] - lat) * 1.2) ** 2)

    out = {}
    for s in SEGMENTS:
        if s.id not in ("BSM-DSU", "DSU-CHL", "CHL-JGL"):
            out[s.id] = [[lon, lat] for lat, lon in s.route]
            continue
        a, b = nearest(*s.route[0]), nearest(*s.route[-1])
        step = 1 if b >= a else -1
        mid = [pts[i] for i in range(a, b + step, step)]
        out[s.id] = [[s.route[0][1], s.route[0][0]], *mid[1:-1], [s.route[-1][1], s.route[-1][0]]]
    return out


def main(src: str):
    topo = json.loads(Path(src).read_text())
    rivers = [c for l in lines_of(topo, "rivers") for c in clip(l)]
    # Indus = the long line that passes near Chilas
    indus = [l for l in rivers if any(abs(p[0] - 74.1) < 0.15 and abs(p[1] - 35.42) < 0.15 for p in l)]
    indus.sort(key=lambda l: min(p[0] for p in l))
    indus_flat = [p for l in indus for p in l]
    if indus_flat and indus_flat[0][0] > indus_flat[-1][0]:
        indus_flat.reverse()
    # International boundaries in the far north only (China, Afghanistan / Wakhan)
    borders = [c for l in lines_of(topo, "countries") for c in clip(l) if all(p[1] > 36.35 for p in c)]
    routes = snap_routes([indus_flat])
    geo = {
        "bbox": BBOX,
        "rivers": [{"name": "Indus", "coords": indus_flat}],
        "lakes": [{"name": "Attabad Lake", "coords": ATTABAD_LAKE}],
        "borders": borders,
        "peaks": [{"name": n, "lat": la, "lon": lo, "elev": e} for n, la, lo, e in PEAKS],
        "towns": [{"name": n, "lat": la, "lon": lo, "rank": r} for n, la, lo, r in TOWNS],
        "labels": [{"name": "China", "lat": 37.0, "lon": 75.75}],
        "routes": routes,
        "credits": "Rivers and boundaries: Natural Earth (public domain). Route: approximate, snapped to the Indus.",
    }
    OUT.write_text(json.dumps(geo, separators=(",", ":")))
    print(OUT, OUT.stat().st_size // 1024, "KB;", "indus pts", len(indus_flat), "borders", len(borders),
          {k: len(v) for k, v in routes.items()})


if __name__ == "__main__":
    main(sys.argv[1])
