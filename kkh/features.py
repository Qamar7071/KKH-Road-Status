"""
Feature engineering — ONE function used by both training and live prediction, so there
is no train/serve skew.

Row = (date t, segment). Question answered: "will this segment be blocked on t+1?"
Information available on the evening of day t:
  * weather observed up to t  + forecast for t+1
  * news / community reports up to t
  * landslide history up to t-7 (catalogues lag in reality)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from kkh.config import SEGMENTS, SEGMENT_IDS
from kkh.data.news import classify_text

SEG_STATIC = pd.DataFrame([{
    "segment_id": s.id, "elevation_m": s.elevation_m, "glacier_fed": int(s.glacier_fed),
    "susceptibility": s.susceptibility, "seg_code": i,
} for i, s in enumerate(SEGMENTS)])

FEATURE_GROUPS = {
    "Tomorrow's rain/snow forecast": ["f_precip", "f_snow", "f_phours", "storm_total", "f_wind"],
    "Rain in the last days": ["precip_t", "rain3", "rain7", "rain14", "rain30"],
    "Snow & freeze-thaw": ["snow7", "snow30", "f_freeze_thaw", "ft_days7", "f_tmin"],
    "Heat / glacier melt": ["f_tmax", "tmax3", "f_melt_excess", "melt3"],
    "News & traveller reports": ["news_block_t", "news_open_t", "news_block_3d", "news_generic_block_t",
                                 "news_net", "days_since_report"],
    "Landslide history": ["hist_365", "hist_90", "days_since_event"],
    "Season": ["month_sin", "month_cos", "doy_sin", "doy_cos", "is_monsoon"],
}
FEATURES = [f for g in FEATURE_GROUPS.values() for f in g] + \
           ["elevation_m", "glacier_fed", "susceptibility", "seg_code"]


FORECAST_COLS = ["f_precip", "f_snow", "f_phours", "f_wind", "f_tmax", "f_tmin"]


def add_forecast_noise(X: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    """Training-only: replace 'perfect' t+1 weather with a realistic 24h forecast.

    History gives us what actually fell tomorrow; live we only have a forecast. Day-1
    precipitation forecasts in mountains are roughly ±40-60% (multiplicative) and miss
    some showers; temperatures are ~±1.5 °C. Training on noisy inputs keeps the model
    from over-trusting the forecast.
    """
    rng = np.random.default_rng(seed)
    X = X.copy()
    n = len(X)
    mult = rng.lognormal(0, 0.45, n)
    for c in ("f_precip", "f_snow"):
        X[c] = (X[c] * mult).round(1)
    missed = rng.random(n) < 0.12                      # forecast misses a small shower
    X.loc[missed & (X["f_precip"] < 8), ["f_precip", "f_snow"]] = 0.0
    spurious = (X["f_precip"] == 0) & (rng.random(n) < 0.06)   # false light rain
    X.loc[spurious, "f_precip"] = rng.gamma(1.0, 2.0, int(spurious.sum())).round(1)
    X["f_phours"] = np.where(X["f_precip"] > 0, np.clip(X["f_phours"] * mult, 1, 24), 0).round(0)
    X["f_tmax"] = X["f_tmax"] + rng.normal(0, 1.5, n)
    X["f_tmin"] = X["f_tmin"] + rng.normal(0, 1.5, n)
    X["f_wind"] = (X["f_wind"] * rng.lognormal(0, 0.2, n)).round(1)
    heat_thr = 33 - 6.5 * (X["elevation_m"] - 900) / 1000
    X["f_melt_excess"] = (X["f_tmax"] - heat_thr).clip(lower=0) * X["glacier_fed"]
    X["f_freeze_thaw"] = ((X["f_tmin"] < 0) & (X["f_tmax"] > 2)).astype(int)
    X["storm_total"] = X["precip_t"] + X["f_precip"]
    return X


def explain_groups(contrib: dict[str, float]) -> list[dict]:
    """Collapse per-feature contributions into the human-readable groups above."""
    out = []
    for name, cols in FEATURE_GROUPS.items():
        v = float(sum(contrib.get(c, 0.0) for c in cols))
        out.append({"group": name, "impact": round(v, 4)})
    return sorted(out, key=lambda d: -abs(d["impact"]))


# ----------------------------------------------------------------- news aggregation
def aggregate_news(items: pd.DataFrame) -> pd.DataFrame:
    """items: date + title (+ summary). Returns per (date, segment) report counts."""
    cols = ["date", "segment_id", "news_block", "news_open", "news_generic_block", "news_generic_open"]
    if items is None or not len(items):
        return pd.DataFrame(columns=cols)
    recs = []
    for d, title, summ in zip(items["date"], items["title"],
                              items.get("summary", pd.Series([""] * len(items)))):
        c = classify_text(f"{title}. {summ or ''}")
        if c["status"] == "none":
            continue
        is_block = c["status"] == "blocked"
        is_open = c["status"] in ("open", "partial")
        targets = c["segments"] or SEGMENT_IDS
        generic = not c["segments"]
        for sid in targets:
            recs.append({"date": pd.Timestamp(d).normalize(), "segment_id": sid,
                         "news_block": 0 if generic else int(is_block),
                         "news_open": 0 if generic else int(is_open),
                         "news_generic_block": int(generic and is_block),
                         "news_generic_open": int(generic and is_open)})
    if not recs:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(recs).groupby(["date", "segment_id"], as_index=False).sum()


# ----------------------------------------------------------------- main builder
def build_features(weather: pd.DataFrame, news_daily: pd.DataFrame | None,
                   history: pd.DataFrame | None, labels: pd.DataFrame | None = None) -> pd.DataFrame:
    w = weather.copy()
    w["date"] = pd.to_datetime(w["date"])
    w = w.sort_values(["segment_id", "date"]).merge(SEG_STATIC, on="segment_id")
    g = w.groupby("segment_id", group_keys=False)

    def roll(col, n, shift=0):
        return g[col].transform(lambda s: s.shift(shift).rolling(n, min_periods=1).sum())

    heat_thr = 33 - 6.5 * (w["elevation_m"] - 900) / 1000
    w["melt_excess"] = (w["temperature_2m_max"] - heat_thr).clip(lower=0) * w["glacier_fed"]
    w["ft"] = ((w["temperature_2m_min"] < 0) & (w["temperature_2m_max"] > 2)).astype(int)

    # forecast for tomorrow (t+1)
    nxt = lambda c: g[c].shift(-1)
    w["f_precip"], w["f_snow"] = nxt("precipitation_sum"), nxt("snowfall_sum")
    w["f_tmax"], w["f_tmin"] = nxt("temperature_2m_max"), nxt("temperature_2m_min")
    w["f_phours"], w["f_wind"] = nxt("precipitation_hours"), nxt("wind_speed_10m_max")
    w["f_melt_excess"], w["f_freeze_thaw"] = nxt("melt_excess"), nxt("ft")

    # antecedent conditions up to t
    w["precip_t"] = w["precipitation_sum"]
    for n in (3, 7, 14, 30):
        w[f"rain{n}"] = roll("precipitation_sum", n)
    w["snow7"], w["snow30"] = roll("snowfall_sum", 7), roll("snowfall_sum", 30)
    w["ft_days7"] = roll("ft", 7)
    w["tmax3"] = g["temperature_2m_max"].transform(lambda s: s.rolling(3, min_periods=1).mean())
    w["melt3"] = roll("melt_excess", 3)
    w["storm_total"] = w["precip_t"] + w["f_precip"]

    # season
    m, doy = w["date"].dt.month, w["date"].dt.dayofyear
    w["month_sin"], w["month_cos"] = np.sin(2 * np.pi * m / 12), np.cos(2 * np.pi * m / 12)
    w["doy_sin"], w["doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    w["is_monsoon"] = m.isin([7, 8, 9]).astype(int)

    w = _add_news(w, news_daily)
    w = _add_history(w, history)

    if labels is not None:
        lab = labels[["date", "segment_id", "closed"] + (["seasonal"] if "seasonal" in labels else [])].copy()
        lab["date"] = pd.to_datetime(lab["date"]) - pd.Timedelta(days=1)   # label of t+1 -> row t
        lab = lab.rename(columns={"closed": "target", "seasonal": "seasonal_next"})
        w = w.merge(lab, on=["date", "segment_id"], how="inner")
    return w


def _add_news(w: pd.DataFrame, nd: pd.DataFrame | None) -> pd.DataFrame:
    base = ["news_block", "news_open", "news_generic_block", "news_generic_open"]
    if nd is not None and len(nd):
        nd = nd.copy()
        nd["date"] = pd.to_datetime(nd["date"])
        w = w.merge(nd, on=["date", "segment_id"], how="left")
    for c in base:
        if c not in w:
            w[c] = 0
        w[c] = w[c].fillna(0)
    g = w.groupby("segment_id", group_keys=False)
    w["news_block_t"], w["news_open_t"] = w["news_block"], w["news_open"]
    w["news_generic_block_t"] = w["news_generic_block"]
    w["news_block_3d"] = g["news_block"].transform(lambda s: s.rolling(3, min_periods=1).sum())
    # exponentially-decayed "net status": >0 means recent reports say blocked
    net = (w["news_block"] + 0.3 * w["news_generic_block"]) - (w["news_open"] + 0.3 * w["news_generic_open"])
    w["_net"] = net
    w["news_net"] = w.groupby("segment_id")["_net"].transform(lambda s: s.ewm(halflife=1.0).mean())
    any_rep = (w["news_block"] + w["news_open"]) > 0
    w["_rep_date"] = w["date"].where(any_rep)
    last = w.groupby("segment_id")["_rep_date"].ffill()
    w["days_since_report"] = (w["date"] - last).dt.days.fillna(60).clip(upper=60)
    return w.drop(columns=["_net", "_rep_date"])


def _add_history(w: pd.DataFrame, hist: pd.DataFrame | None, lag_days: int = 7) -> pd.DataFrame:
    w["hist_365"] = 0.0
    w["hist_90"] = 0.0
    w["days_since_event"] = 365.0
    if hist is None or not len(hist):
        return w
    h = hist.copy()
    h["date"] = pd.to_datetime(h["date"]).dt.normalize()
    out = []
    for sid, ws in w.groupby("segment_id"):
        ev = np.sort(h.loc[h["segment_id"] == sid, "date"].values.astype("datetime64[D]"))
        t = ws["date"].values.astype("datetime64[D]") - np.timedelta64(lag_days, "D")
        hi = np.searchsorted(ev, t, side="right")
        ws = ws.copy()
        ws["hist_365"] = hi - np.searchsorted(ev, t - np.timedelta64(365, "D"), side="right")
        ws["hist_90"] = hi - np.searchsorted(ev, t - np.timedelta64(90, "D"), side="right")
        last = np.where(hi > 0, ev[np.maximum(hi - 1, 0)], np.datetime64("NaT", "D"))
        dse = (t - last).astype("timedelta64[D]").astype(float) + lag_days
        ws["days_since_event"] = np.nan_to_num(dse, nan=365.0).clip(max=365)
        out.append(ws)
    return pd.concat(out).sort_values(["segment_id", "date"])
