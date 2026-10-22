"""
Landslide / closure history along the KKH corridor.

Sources (merged):
  1. NASA Global Landslide Catalog (COOLR) — points filtered to within 15 km of the KKH route
  2. data/labels/documented_events.csv — hand-verified closures from news reports (with links)
  3. Closures detected in the news archive (kkh.data.news)
Output: one row per (date, segment_id) event, used for "history" features.
"""
from __future__ import annotations

import io
import math

import numpy as np
import pandas as pd
import requests

from kkh.config import LABEL_DIR, NASA_GLC_URL, RAW_DIR, SEGMENTS, USER_AGENT

CORRIDOR_KM = 15.0


def _km(lat1, lon1, lat2, lon2):
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371.0 * math.hypot(x, y)


def _dist_to_route(lat, lon, route) -> float:
    """Approximate distance (km) from a point to a polyline (equirectangular projection)."""
    best = float("inf")
    for (a_lat, a_lon), (b_lat, b_lon) in zip(route[:-1], route[1:]):
        kx = 111.32 * math.cos(math.radians(lat))
        ax, ay = (a_lon - lon) * kx, (a_lat - lat) * 110.57
        bx, by = (b_lon - lon) * kx, (b_lat - lat) * 110.57
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def nearest_segment(lat: float, lon: float) -> tuple[str | None, float]:
    d = [(_dist_to_route(lat, lon, s.route), s.id) for s in SEGMENTS]
    dist, sid = min(d)
    return (sid if dist <= CORRIDOR_KM else None), dist


def _from_raw(raw: pd.DataFrame) -> pd.DataFrame:
    raw = raw.copy()
    raw.columns = [c.lower().strip() for c in raw.columns]
    lat = next(c for c in raw.columns if c.startswith("lat"))
    lon = next(c for c in raw.columns if c.startswith("lon"))
    dcol = next((c for c in ("event_date", "date", "event_time") if c in raw.columns), None)
    rows = []
    for _, ev in raw.iterrows():
        sid, _ = nearest_segment(float(ev[lat]), float(ev[lon]))
        if sid and dcol:
            rows.append({"date": pd.to_datetime(ev.get(dcol), errors="coerce"), "segment_id": sid, "source": "nasa_glc",
                         "detail": str(ev.get("event_title") or ev.get("location_description") or "")})
    df = pd.DataFrame(rows, columns=["date", "segment_id", "source", "detail"])
    if len(df):
        df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce").dt.tz_localize(None).dt.normalize()
    return df.dropna(subset=["date"])


def load_nasa_catalog(download: bool = True) -> pd.DataFrame:
    cache = RAW_DIR / "nasa_glc_kkh.csv"
    if cache.exists():
        return pd.read_csv(cache, parse_dates=["date"])
    raw_file = RAW_DIR / "nasa_glc_raw_kkh.csv"            # written by scripts/fetch_real_data.py
    if raw_file.exists():
        df = _from_raw(pd.read_csv(raw_file, low_memory=False))
        df.to_csv(cache, index=False)
        return df
    if not download:
        return pd.DataFrame(columns=["date", "segment_id", "source", "detail"])
    r = requests.get(NASA_GLC_URL, timeout=120, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    raw = pd.read_csv(io.StringIO(r.text), low_memory=False)
    raw.columns = [c.lower().strip() for c in raw.columns]
    raw = raw[(raw["latitude"].between(33.8, 37.2)) & (raw["longitude"].between(72.5, 75.8))]
    df = _from_raw(raw)
    df.to_csv(cache, index=False)
    return df


def load_documented_events() -> pd.DataFrame:
    """Expand documented_events.csv into one row per (date, segment)."""
    ev = pd.read_csv(LABEL_DIR / "documented_events.csv", parse_dates=["date_start", "date_end"])
    rows = []
    for _, e in ev.iterrows():
        for d in pd.date_range(e.date_start, e.date_end):
            for sid in str(e.segment_ids).split(";"):
                rows.append({"date": d, "segment_id": sid.strip(), "source": "documented",
                             "detail": f"{e.cause}: {e.places}", "url": e.source_url})
    return pd.DataFrame(rows)


def build_history(extra: pd.DataFrame | None = None, use_nasa: bool = True) -> pd.DataFrame:
    parts = [load_documented_events()]
    if use_nasa:
        try:
            parts.append(load_nasa_catalog())
        except requests.RequestException:
            pass
    if extra is not None and len(extra):
        parts.append(extra)
    hist = pd.concat(parts, ignore_index=True)
    hist["date"] = pd.to_datetime(hist["date"]).dt.normalize()
    hist = hist.drop_duplicates(["date", "segment_id"]).sort_values("date")
    hist.to_csv(RAW_DIR / "landslide_history.csv", index=False)
    return hist


def susceptibility_from_history(hist: pd.DataFrame) -> dict[str, float]:
    """Events per 100 km per year, blended with the expert prior from config."""
    if hist.empty:
        return {s.id: s.susceptibility for s in SEGMENTS}
    years = max(1.0, (hist["date"].max() - hist["date"].min()).days / 365.25)
    counts = hist.groupby("segment_id").size()
    rate = {s.id: counts.get(s.id, 0) / years / s.length_km * 100 for s in SEGMENTS}
    mx = max(rate.values()) or 1.0
    return {s.id: float(np.clip(0.5 * s.susceptibility + 0.5 * rate[s.id] / mx, 0, 1)) for s in SEGMENTS}
