"""
Download every REAL data source the project uses into data/raw/ (runs on GitHub Actions,
which has open internet; see .github/workflows/fetch-data.yml).

    python scripts/fetch_real_data.py [--only weather,nasa,gdelt,osm,terrain,photos]

Sources
  weather   Open-Meteo historical archive (ERA5 reanalysis), 2015 -> 2 days ago, 8 KKH points
  nasa      NASA Global Landslide Catalog, clipped to the KKH corridor
  gdelt     GDELT news archive (2017 -> now): every article about the KKH being blocked /
            reopened, plus the opening paragraphs of each article (for place names)
  osm       OpenStreetMap: the real N-35 road geometry, Indus/Hunza/Gilgit rivers, Attabad lake
  terrain   AWS Terrain Tiles (SRTM-based DEM) for a shaded-relief basemap
  photos    Wikimedia Commons photos of each segment, with author + licence metadata

Writes data/raw/fetch_manifest.json with what succeeded, so nothing silently falls back.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import io
import json
import math
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kkh.config import RAW_DIR, SEGMENTS, WEATHER_VARS  # noqa: E402

UA = "KKH-Road-Status-Predictor/1.0 (student research project; github.com/Qamar7071)"
S = requests.Session()
S.headers["User-Agent"] = UA
BBOX = (33.9, 72.6, 37.2, 75.9)          # south, west, north, east
MANIFEST: dict = {}


def log(*a):
    print(f"[{datetime.now():%H:%M:%S}]", *a, flush=True)


def get(url, *, params=None, timeout=60, retries=4, **kw):
    last = None
    for i in range(retries):
        try:
            r = S.get(url, params=params, timeout=timeout, **kw)
            if r.status_code == 429:
                time.sleep(10 * (i + 1)); continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(3 * (i + 1))
    raise last


# ------------------------------------------------------------------ weather
def fetch_weather():
    end = (date.today() - timedelta(days=2)).isoformat()
    frames = []
    import pandas as pd
    for year in range(2015, date.today().year + 1):
        y0, y1 = f"{year}-01-01", min(f"{year}-12-31", end)
        p = {"latitude": ",".join(str(s.lat) for s in SEGMENTS), "longitude": ",".join(str(s.lon) for s in SEGMENTS),
             "elevation": ",".join(str(s.elevation_m) for s in SEGMENTS), "daily": ",".join(WEATHER_VARS),
             "timezone": "Asia/Karachi", "start_date": y0, "end_date": y1}
        js = get("https://archive-api.open-meteo.com/v1/archive", params=p, timeout=120).json()
        js = js if isinstance(js, list) else [js]
        for seg, j in zip(SEGMENTS, js):
            d = pd.DataFrame(j["daily"]).rename(columns={"time": "date"})
            d["segment_id"] = seg.id
            frames.append(d)
        log("weather", year, "ok")
        time.sleep(2)
    df = pd.concat(frames)[["date", "segment_id", *WEATHER_VARS]].sort_values(["segment_id", "date"])
    df.to_csv(RAW_DIR / "weather_history.csv", index=False)
    return {"rows": len(df), "start": df.date.min(), "end": df.date.max()}


# ------------------------------------------------------------------ NASA landslides
NASA_URLS = [
    "https://data.nasa.gov/api/views/dd9e-wu2v/rows.csv?accessType=DOWNLOAD",
    "https://data.nasa.gov/resource/dd9e-wu2v.csv?$limit=50000",
    "https://data.nasa.gov/docs/legacy/Global_Landslide_Catalog_Export.csv",
    "https://maps.nccs.nasa.gov/arcgis/sharing/content/items/eec7aee8d2e040c7b8d3ee5fd0e0d7b9/data",
]


def fetch_nasa():
    import pandas as pd
    errors = []
    for url in NASA_URLS:
        try:
            r = get(url, timeout=180, retries=2)
            raw = pd.read_csv(io.StringIO(r.text), low_memory=False)
            raw.columns = [c.lower().strip() for c in raw.columns]
            lat = next(c for c in raw.columns if c.startswith("lat"))
            lon = next(c for c in raw.columns if c.startswith("lon"))
            sub = raw[raw[lat].between(BBOX[0], BBOX[2]) & raw[lon].between(BBOX[1], BBOX[3])]
            sub.to_csv(RAW_DIR / "nasa_glc_raw_kkh.csv", index=False)
            log("nasa ok", url, len(raw), "->", len(sub))
            return {"url": url, "global_rows": len(raw), "kkh_rows": len(sub)}
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {type(e).__name__}: {e}"[:200])
            log("nasa fail", url, e)
    raise RuntimeError("; ".join(errors))


# ------------------------------------------------------------------ GDELT news archive
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_QUERIES = [
    '"Karakoram Highway" (blocked OR closed OR landslide OR restored OR reopened OR suspended OR stranded OR flood)',
    '"KKH" (blocked OR landslide OR restored OR reopened OR closed) (Gilgit OR Hunza OR Kohistan OR Chilas OR Besham)',
    'sourcelang:urdu "شاہراہ قراقرم"',
]


def _months(start: date, end: date):
    d = start
    while d <= end:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield d, min(nxt - timedelta(days=1), end)
        d = nxt


def _article_text(url: str) -> str:
    try:
        r = S.get(url, timeout=15)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return ""
        html = r.text
        html = re.sub(r"(?is)<(script|style|noscript|header|footer|nav)[^>]*>.*?</\1>", " ", html)
        paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", html)
        text = " ".join(unescape(re.sub(r"<[^>]+>", " ", p)) for p in paras)
        return re.sub(r"\s+", " ", text).strip()[:2500]
    except Exception:  # noqa: BLE001
        return ""


def fetch_gdelt():
    out_path = RAW_DIR / "gdelt_articles.jsonl"
    seen, items = set(), []
    for q in GDELT_QUERIES:
        for a, b in _months(date(2017, 1, 1), date.today()):
            p = {"query": q, "mode": "artlist", "format": "json", "maxrecords": 250, "sort": "datedesc",
                 "startdatetime": a.strftime("%Y%m%d000000"), "enddatetime": b.strftime("%Y%m%d235959")}
            try:
                r = get(GDELT, params=p, timeout=60, retries=3)
                arts = r.json().get("articles", []) if r.text.strip().startswith("{") else []
            except Exception as e:  # noqa: BLE001
                log("gdelt fail", a, e); arts = []
            for art in arts:
                if art["url"] in seen:
                    continue
                seen.add(art["url"])
                items.append({"url": art["url"], "title": art.get("title", ""), "seendate": art.get("seendate"),
                              "domain": art.get("domain"), "language": art.get("language"),
                              "country": art.get("sourcecountry")})
            log("gdelt", q[:22], a, len(arts), "total", len(items))
            time.sleep(5.5)          # GDELT asks for <= 1 request / 5 s
    # Opening paragraphs: place names ("Tatta Pani", "Lotar") usually live in the body, not the title
    log("fetching article text for", len(items))
    with cf.ThreadPoolExecutor(12) as ex:
        for it, txt in zip(items, ex.map(lambda i: _article_text(i["url"]), items)):
            it["summary"] = txt
    with out_path.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    return {"articles": len(items), "with_text": sum(bool(i["summary"]) for i in items)}


# ------------------------------------------------------------------ OpenStreetMap
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]


def fetch_osm():
    s, w, n, e = BBOX
    q = f"""[out:json][timeout:240];
(
  way["highway"]["ref"~"^(N-?35|KKH)$"]({s},{w},{n},{e});
  way["highway"]["name"~"Karakoram",i]({s},{w},{n},{e});
)->.road;
(
  way["waterway"="river"]["name"~"^(Indus|Hunza|Gilgit|Khunjerab|Siran|Kunhar)",i]({s},{w},{n},{e});
)->.rivers;
(
  way["natural"="water"]["name"~"Attabad|Ataabad",i]({s},{w},{n},{e});
  relation["natural"="water"]["name"~"Attabad|Ataabad",i]({s},{w},{n},{e});
)->.lakes;
(
  node["place"~"^(city|town)$"]({s},{w},{n},{e});
)->.places;
.road out geom; .rivers out geom; .lakes out geom; .places out;"""
    last = None
    for url in OVERPASS:
        try:
            r = S.post(url, data={"data": q}, timeout=300)
            r.raise_for_status()
            js = r.json()
            feats = []
            for el in js["elements"]:
                tags = el.get("tags", {})
                if el["type"] == "node":
                    feats.append({"type": "Feature", "properties": {"kind": "place", "name": tags.get("name:en") or tags.get("name"), "place": tags.get("place")},
                                  "geometry": {"type": "Point", "coordinates": [round(el["lon"], 5), round(el["lat"], 5)]}})
                    continue
                geoms = [el["geometry"]] if "geometry" in el else [m["geometry"] for m in el.get("members", []) if "geometry" in m]
                kind = ("road" if tags.get("highway") else "river" if tags.get("waterway") else "lake")
                for g in geoms:
                    feats.append({"type": "Feature", "properties": {"kind": kind, "name": tags.get("name:en") or tags.get("name"), "ref": tags.get("ref")},
                                  "geometry": {"type": "LineString", "coordinates": [[round(p["lon"], 5), round(p["lat"], 5)] for p in g]}})
            (RAW_DIR / "osm_kkh.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
            kinds = {}
            for f in feats:
                kinds[f["properties"]["kind"]] = kinds.get(f["properties"]["kind"], 0) + 1
            log("osm ok", kinds)
            return {"url": url, "features": kinds}
        except Exception as e:  # noqa: BLE001
            last = e; log("osm fail", url, e)
    raise last


# ------------------------------------------------------------------ terrain
def _tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def fetch_terrain(z: int = 9):
    s, w, n, e = BBOX
    x0, y0 = _tile(n, w, z)
    x1, y1 = _tile(s, e, z)
    d = RAW_DIR / "terrain" / str(z)
    d.mkdir(parents=True, exist_ok=True)
    jobs = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]

    def one(xy):
        x, y = xy
        f = d / f"{x}_{y}.png"
        if not f.exists():
            f.write_bytes(get(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png", timeout=60).content)
        return f

    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(one, jobs))
    meta = {"z": z, "x": [x0, x1], "y": [y0, y1], "tiles": len(jobs),
            "source": "AWS Terrain Tiles (Mapzen/Tilezen; SRTM, GMTED, ETOPO1)"}
    (d / "meta.json").write_text(json.dumps(meta))
    log("terrain ok", meta)
    return meta


# ------------------------------------------------------------------ photos
PHOTO_QUERIES = {
    "HZR-BSM": ["Karakoram Highway Thakot", "Thakot bridge Indus", "Besham Karakoram Highway"],
    "BSM-DSU": ["Karakoram Highway Kohistan Indus", "Pattan Kohistan", "Karakoram Highway Dasu"],
    "DSU-CHL": ["Karakoram Highway Shatial", "Karakoram Highway Kohistan", "Indus gorge Kohistan road"],
    "CHL-JGL": ["Karakoram Highway Chilas", "Raikot bridge", "Karakoram Highway Nanga Parbat view"],
    "JGL-GLT": ["Junction of three mountain ranges", "Karakoram Highway Jaglot", "Gilgit Karakoram Highway"],
    "GLT-HNZ": ["Karakoram Highway Hunza", "Rakaposhi view point Karakoram Highway", "Karimabad Hunza"],
    "HNZ-SST": ["Attabad Lake", "Passu Cones", "Karakoram Highway Passu"],
    "SST-KJB": ["Khunjerab Pass", "Khunjerab Pass Karakoram Highway", "Karakoram Highway Sost"],
}
FREE = re.compile(r"^(cc0|cc by|cc-by|public domain|pd)", re.I)


def fetch_photos(per_seg: int = 4):
    from PIL import Image
    d = RAW_DIR / "photos"
    d.mkdir(parents=True, exist_ok=True)
    meta = []
    for sid, queries in PHOTO_QUERIES.items():
        got, seen = 0, set()
        for q in queries:
            if got >= per_seg:
                break
            p = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrsearch": q,
                 "gsrlimit": 10, "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 1200}
            try:
                pages = get("https://commons.wikimedia.org/w/api.php", params=p).json().get("query", {}).get("pages", {})
            except Exception as e:  # noqa: BLE001
                log("photos fail", q, e); continue
            for pg in sorted(pages.values(), key=lambda x: x.get("index", 99)):
                ii = (pg.get("imageinfo") or [{}])[0]
                em = ii.get("extmetadata", {})
                lic = em.get("LicenseShortName", {}).get("value", "")
                if pg["title"] in seen or ii.get("mime") not in ("image/jpeg", "image/png") or not FREE.match(lic):
                    continue
                if ii.get("width", 0) < ii.get("height", 1) * 1.15:      # landscape only
                    continue
                seen.add(pg["title"])
                try:
                    img = Image.open(io.BytesIO(get(ii["thumburl"], timeout=60).content)).convert("RGB")
                except Exception:  # noqa: BLE001
                    continue
                img.thumbnail((1200, 800))
                name = f"{sid}_{got}.jpg"
                img.save(d / name, quality=82, optimize=True, progressive=True)
                artist = re.sub(r"<[^>]+>", "", unescape(em.get("Artist", {}).get("value", ""))).strip()
                meta.append({"segment_id": sid, "file": name, "title": pg["title"], "query": q,
                             "author": artist[:120], "license": lic, "license_url": em.get("LicenseUrl", {}).get("value"),
                             "source": ii.get("descriptionurl"),
                             "description": re.sub(r"<[^>]+>", "", unescape(em.get("ImageDescription", {}).get("value", "")))[:300]})
                got += 1
                log("photo", sid, pg["title"], lic)
                if got >= per_seg:
                    break
            time.sleep(1)
    (d / "photos.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    return {"photos": len(meta)}


STEPS = {"weather": fetch_weather, "nasa": fetch_nasa, "osm": fetch_osm, "terrain": fetch_terrain,
         "photos": fetch_photos, "gdelt": fetch_gdelt}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(STEPS))
    which = ap.parse_args().only.split(",")
    mf = RAW_DIR / "fetch_manifest.json"
    MANIFEST = json.loads(mf.read_text()) if mf.exists() else {}
    for k in which:
        t = time.time()
        try:
            res = STEPS[k]()
            MANIFEST[k] = {"ok": True, **{a: str(b) if not isinstance(b, (int, float, list, dict)) else b for a, b in res.items()}}
        except Exception as e:  # noqa: BLE001
            MANIFEST[k] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}
            log(k, "FAILED", e)
        MANIFEST[k]["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        MANIFEST[k]["seconds"] = round(time.time() - t)
        mf.write_text(json.dumps(MANIFEST, indent=2, ensure_ascii=False))
    print(json.dumps(MANIFEST, indent=2, ensure_ascii=False))
