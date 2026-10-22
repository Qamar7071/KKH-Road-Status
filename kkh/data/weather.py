"""
Weather data from Open-Meteo (free, no API key).

- Historical: ERA5-based archive API  -> training data back to 2015
- Live: forecast API with past_days    -> today's antecedent conditions + tomorrow's forecast
All segments are requested in one call (Open-Meteo accepts comma-separated coordinates).
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from kkh.config import RAW_DIR, SEGMENTS, USER_AGENT, WEATHER_VARS

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def _request(url: str, params: dict, retries: int = 3, timeout: int = 30) -> list[dict]:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            js = r.json()
            return js if isinstance(js, list) else [js]
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise ConnectionError(f"Open-Meteo request failed: {last}")


def _to_frame(payloads: list[dict]) -> pd.DataFrame:
    frames = []
    for seg, js in zip(SEGMENTS, payloads):
        d = pd.DataFrame(js["daily"]).rename(columns={"time": "date"})
        d["date"] = pd.to_datetime(d["date"])
        d["segment_id"] = seg.id
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    return df[["date", "segment_id", *WEATHER_VARS]]


def _base_params() -> dict:
    return {
        "latitude": ",".join(str(s.lat) for s in SEGMENTS),
        "longitude": ",".join(str(s.lon) for s in SEGMENTS),
        "elevation": ",".join(str(s.elevation_m) for s in SEGMENTS),
        "daily": ",".join(WEATHER_VARS),
        "timezone": "Asia/Karachi",
    }


def fetch_history(start: str, end: str) -> pd.DataFrame:
    """Daily history for all segments. Splits into yearly chunks to stay polite."""
    chunks = []
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    for year in range(s.year, e.year + 1):
        y0 = max(s, pd.Timestamp(year=year, month=1, day=1))
        y1 = min(pd.Timestamp(year=year, month=12, day=31), e)
        p = _base_params() | {"start_date": y0.date().isoformat(), "end_date": y1.date().isoformat()}
        chunks.append(_to_frame(_request(ARCHIVE_URL, p, timeout=90)))
        time.sleep(1)
    df = pd.concat(chunks).drop_duplicates(["date", "segment_id"]).sort_values(["segment_id", "date"])
    df.to_csv(RAW_DIR / "weather_history.csv", index=False)
    return df


def fetch_live(past_days: int = 92, forecast_days: int = 3, retries: int = 2,
               timeout: int = 15) -> pd.DataFrame:
    """Recent observations + short-range forecast (today is included).
    past_days=92 (Open-Meteo's maximum) covers the 30-day rain window and the 60-day
    "days since last report" feature, so live features match training."""
    p = _base_params() | {"past_days": past_days, "forecast_days": forecast_days}
    df = _to_frame(_request(FORECAST_URL, p, retries=retries, timeout=timeout))
    df.to_csv(RAW_DIR / "weather_live.csv", index=False)
    return df
