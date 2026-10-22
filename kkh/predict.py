"""
Step 5 — the prediction engine used by the API.

Two ways to ask "will each KKH segment be blocked tomorrow?":

  live()          Open-Meteo forecast + last 40 days of weather, today's news headlines
                  and community reports -> probability for tomorrow, per segment.
  replay(date)    "Time machine": rebuild the evening of any past date from the stored
                  dataset and compare the forecast with what actually happened.

If live sources are unreachable, live() falls back to the latest replay and says so in
`source` / `notice`, so the UI never pretends offline data is live.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import joblib
import numpy as np
import pandas as pd

from kkh import store
from kkh.config import PROFILE, PROCESSED_DIR, SEG_BY_ID, SEGMENTS, in_seasonal_closure, risk_band
from kkh.data import news as news_mod
from kkh.data import weather as weather_mod
from kkh.features import FEATURE_GROUPS, FEATURES, aggregate_news, build_features
from kkh.model import EXPORT_PATH, MODEL_PATH, REPORT_PATH

LIVE_TTL_S = 30 * 60
_lock = threading.Lock()


def _pk_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=5)


def _r(x, n=1):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


class Predictor:
    def __init__(self):
        self.model = joblib.load(MODEL_PATH)
        self.report = json.loads(REPORT_PATH.read_text())
        self.meta = json.loads((PROCESSED_DIR / "meta.json").read_text())
        rd = lambda f: pd.read_csv(PROCESSED_DIR / f, parse_dates=["date"])
        self.weather, self.news_daily = rd("weather.csv"), rd("news_daily.csv")
        self.history, self.labels = rd("history.csv"), rd("labels.csv")
        self._live_cache: tuple[float, dict] | None = None

    # ------------------------------------------------------------------ public API
    @property
    def data_range(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        d = self.weather["date"]
        return d.min() + pd.Timedelta(days=30), d.max() - pd.Timedelta(days=1)

    def replay(self, as_of: str | pd.Timestamp) -> dict:
        t = pd.Timestamp(as_of).normalize()
        lo, hi = self.data_range
        if not lo <= t <= hi:
            raise ValueError(f"date must be between {lo.date()} and {hi.date()}")
        # 95 days of context: news features join onto weather dates, and "days since last report"
        # looks back up to 60 days, so a shorter window silently changed that feature (audit finding)
        w = self.weather[(self.weather.date >= t - pd.Timedelta(days=95)) & (self.weather.date <= t + pd.Timedelta(days=1))]
        # 90 days of news so "days since last report" (capped at 60) matches training exactly
        nd = self.news_daily[(self.news_daily.date >= t - pd.Timedelta(days=90)) & (self.news_daily.date <= t)]
        actual = self.labels[self.labels.date == t + pd.Timedelta(days=1)].set_index("segment_id")
        res = self._predict(w, nd, t, source=f"replay ({self.meta.get('mode', 'unknown')} dataset)",
                            actual=actual)
        res["mode"] = "replay"
        return res

    def live(self, force: bool = False) -> dict:
        with _lock:
            if not force and self._live_cache:
                ttl = LIVE_TTL_S if self._live_cache[1].get("mode") == "live" else 120   # retry offline soon
                if time.time() - self._live_cache[0] < ttl:
                    return self._live_cache[1]
            res = self._live_uncached()
            self._live_cache = (time.time(), res)
            return res

    def trend(self, end: str | pd.Timestamp | None = None, days: int = 14) -> list[dict]:
        """Clear-run chance for the `days` evenings up to `end` (replays of stored data), for the sparkline."""
        lo, hi = self.data_range
        end = min(pd.Timestamp(end).normalize(), hi) if end is not None else hi
        key = (end, days)
        cache = self.__dict__.setdefault("_trend_cache", {})
        if key not in cache:
            out = []
            for d in pd.date_range(max(lo, end - pd.Timedelta(days=days - 1)), end):
                r = self.replay(d)
                out.append({"as_of": r["as_of"], "target_date": r["target_date"],
                            "p_clear": r["summary"]["p_full_route_open"],
                            "worst": r["summary"]["worst_probability"],
                            "blocked_actual": sum(1 for s in r["segments"] if s.get("actual", {}).get("closed") and not s["seasonal_closure"])})
            cache[key] = out
        return cache[key]

    def notable_dates(self, n: int = 8) -> list[dict]:
        """Past storm days (heaviest corridor-wide rain/snow) for the time machine.
        Picked by weather, not by outcome, so the demo shows hits AND misses honestly."""
        lab = self.labels[~self.labels.seasonal.astype(bool)]
        blocked = lab.groupby("date").closed.sum()
        w = self.weather.merge(lab[["date", "segment_id"]], on=["date", "segment_id"])   # skip seasonal rows
        w = w.assign(load=w.precipitation_sum + w.snowfall_sum)
        storm = w.groupby("date").load.sum()
        lo, hi = self.data_range
        storm = storm[(storm.index - pd.Timedelta(days=1) >= lo) & (storm.index - pd.Timedelta(days=1) <= hi)]
        out, used = [], []
        for d, _ in storm.sort_values(ascending=False).items():
            k = int(blocked.get(d, 0))
            if any(abs((d - u).days) < 25 for u in used):
                continue
            used.append(d)
            out.append({"as_of": (d - pd.Timedelta(days=1)).date().isoformat(), "target": d.date().isoformat(),
                        "segments_blocked": int(k), "month": d.strftime("%b %Y")})
            if len(out) >= n:
                break
        return sorted(out, key=lambda x: x["as_of"], reverse=True)

    # ------------------------------------------------------------------ internals
    def _live_uncached(self) -> dict:
        today = pd.Timestamp(_pk_now().date())
        try:
            w = weather_mod.fetch_live(past_days=92, forecast_days=3)
        except Exception as e:  # noqa: BLE001
            res = self.replay(self.data_range[1])
            res |= {"mode": "fallback", "notice": f"Live weather unavailable ({type(e).__name__}); "
                    f"showing the latest replay from the stored dataset instead."}
            return res
        headlines, nd_news = [], pd.DataFrame()
        try:
            items = news_mod.fetch_news()
            # fresh items + the stored archive, so features see the same 60+ day news history as training
            seen = {i["id"] for i in items}
            items += [a for a in news_mod.load_archive() if a.get("id") not in seen and a.get("status") != "none"]
            if items:
                df = pd.DataFrame(items)
                df["date"] = pd.to_datetime(df["published"], utc=True).dt.tz_convert("Asia/Karachi") \
                    .dt.tz_localize(None).dt.normalize()
                df = df[df.date >= today - pd.Timedelta(days=90)]
                nd_news = aggregate_news(df)
                headlines = [{"title": r.title, "link": r.link, "date": r.date.date().isoformat(),
                              "status": r.status, "segments": r.segments}
                             for r in df[df.date >= today - pd.Timedelta(days=7)].sort_values("date", ascending=False).head(12).itertuples()]
        except Exception:  # noqa: BLE001 — news is optional
            pass
        nd = pd.concat([nd_news, store.reports_as_news_daily()], ignore_index=True)
        if len(nd):
            nd = nd.groupby(["date", "segment_id"], as_index=False).sum(numeric_only=True)
        res = self._predict(w, nd, today, source="live (Open-Meteo forecast + news + community reports)")
        res |= {"mode": "live", "headlines": headlines}
        store.log_predictions(res["as_of"], res["target_date"],
                              {s["id"]: s["probability"] for s in res["segments"]}, "live")
        return res

    def _predict(self, weather: pd.DataFrame, news_daily: pd.DataFrame, as_of: pd.Timestamp,
                 source: str, actual: pd.DataFrame | None = None) -> dict:
        X = build_features(weather, news_daily, self.history)
        X = X[X.date == as_of]
        if len(X) != len(SEGMENTS) or X[["f_precip", "f_tmax"]].isna().any().any():
            raise ValueError(f"incomplete inputs for {as_of.date()} (need weather for that day and the next)")
        X = X.set_index("segment_id").loc[[s.id for s in SEGMENTS]].reset_index()
        probs = self.model.predict_proba(X)
        expl = self.model.explain(X)
        target = as_of + pd.Timedelta(days=1)

        segs = []
        for i, seg in enumerate(SEGMENTS):
            row = X.iloc[i]
            seasonal = in_seasonal_closure(seg, target)
            p = 1.0 if seasonal else float(probs[i])
            drivers = sorted(({"group": g, "impact": v} for g, v in expl[i].items()), key=lambda d: -abs(d["impact"]))
            rec = {
                "id": seg.id, "name": seg.name, "name_ur": seg.name_ur, "district": seg.district,
                "length_km": seg.length_km, "elevation_m": seg.elevation_m, "notes": seg.notes,
                "probability": round(p, 4), "seasonal_closure": seasonal,
                "band": ({"key": "closed", "label": "Closed for winter", "label_ur": "سردیوں میں بند"}
                         if seasonal else risk_band(p)),
                "drivers": [] if seasonal else drivers[:4],
                "tomorrow": {"precip_mm": _r(row.f_precip), "snow_cm": _r(row.f_snow),
                             "tmax_c": _r(row.f_tmax), "tmin_c": _r(row.f_tmin), "wind_kmh": _r(row.f_wind)},
                "recent": {"rain_3d_mm": _r(row.rain3), "rain_7d_mm": _r(row.rain7), "snow_7d_cm": _r(row.snow7),
                           "news_block_reports_3d": int(row.news_block_3d), "events_365d": int(row.hist_365)},
                "features": {f: round(float(row[f]), 5) for f in FEATURES},   # for the what-if tool
            }
            if actual is not None and seg.id in actual.index:
                a = actual.loc[seg.id]
                cause = a.get("cause")
                rec["actual"] = {"closed": int(a["closed"]), "cause": None if cause is None or pd.isna(cause) else str(cause) or None}
            segs.append(rec)

        open_segs = [s for s in segs if not s["seasonal_closure"]]
        p_clear = float(np.prod([1 - s["probability"] for s in open_segs]))
        worst = max(open_segs, key=lambda s: s["probability"])
        return {
            "as_of": as_of.date().isoformat(), "target_date": target.date().isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": source, "data_mode": self.meta.get("mode"),
            "summary": {"p_full_route_open": round(p_clear, 4), "worst_segment": worst["id"],
                        "worst_probability": worst["probability"],
                        "n_caution": sum(s["band"]["key"] == "caution" for s in segs),
                        "n_blocked": sum(s["band"]["key"] in ("blocked", "closed") for s in segs)},
            "segments": segs,
        }


def route_geojson() -> dict:
    feats = [{"type": "Feature", "properties": {"id": s.id, "name": s.name},
              "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in s.route]}}
             for s in SEGMENTS]
    return {"type": "FeatureCollection", "features": feats}


def profile() -> list[dict]:
    return [{"km": k, "elev": e, "place": n} for k, e, n in PROFILE]


def segment_info() -> list[dict]:
    return [{"id": s.id, "name": s.name, "name_ur": s.name_ur, "district": s.district,
             "places": list(s.places), "length_km": s.length_km, "elevation_m": s.elevation_m,
             "susceptibility": s.susceptibility, "glacier_fed": s.glacier_fed,
             "seasonal_closure": s.seasonal_closure, "route": [list(p) for p in s.route]} for s in SEGMENTS]


def _get_bool_key(d: dict, key: bool):
    for k in (key, str(key), str(key).lower()):
        if k in d:
            return d[k]
    return None


def model_card() -> dict:
    rep = json.loads(REPORT_PATH.read_text())
    rep["feature_groups"] = FEATURE_GROUPS
    rep["export_kb"] = EXPORT_PATH.stat().st_size // 1024 if EXPORT_PATH.exists() else None
    audit_path = EXPORT_PATH.parent / "audit.json"
    if audit_path.exists():
        a = json.loads(audit_path.read_text())
        bt = pd.DataFrame(a.get("backtest", []))
        mono = a.get("monotonicity", {})
        rep["audit"] = {
            "backtest_years": f"{int(bt.year.min())}–{int(bt.year.max())}" if len(bt) else None,
            "backtest_auc_mean": round(float(bt.auc.mean()), 3) if len(bt) else None,
            "backtest_auc_std": round(float(bt.auc.std()), 3) if len(bt) else None,
            "monotone_violations": max((v["violations_pct"] for d in mono.values() for v in d.values()), default=None),
            "replay_matches_training": not a.get("skew"),
            "onset_recall": _get_bool_key(a.get("errors", {}).get("onset", {}).get("caught", {}), True),
            "ongoing_recall": _get_bool_key(a.get("errors", {}).get("onset", {}).get("caught", {}), False),
        }
    return rep


_instance: Predictor | None = None


def get_predictor() -> Predictor:
    global _instance
    if _instance is None:
        _instance = Predictor()
    return _instance


__all__ = ["Predictor", "get_predictor", "route_geojson", "segment_info", "model_card", "SEG_BY_ID"]
