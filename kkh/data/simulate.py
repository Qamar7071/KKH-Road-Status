"""
Bootstrap simulator — used ONLY when real data can't be downloaded (offline / first run).

Why it exists: there is no public, labelled day-by-day KKH closure dataset. The real
pipeline builds one over time from news + documented events (see labels.py). To let the
whole system (features -> model -> API -> UI) run end-to-end on day one, this module
generates a physically-plausible stand-in with the SAME schemas as the real sources:

  weather  : Open-Meteo-like daily variables per segment (monsoon + westerly storms,
             elevation lapse rate, freeze-thaw, heatwaves)
  closures : hazard model driven by rain/snow/melt x segment susceptibility, with
             event durations that grow with storm severity
  news     : English/Urdu headlines generated from closures (with missed reports and
             false alarms) and then parsed by the REAL news classifier
  history  : landslide events derived from simulated closures

Calibration targets (from documented events): Dasu–Chilas–Jaglot is blocked most often,
peaks in Jul–Aug monsoon and Feb–Apr rain/snow; Hazara section rarely; Khunjerab closes
every winter by schedule.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from kkh.config import SEGMENTS, in_seasonal_closure

MONSOON_P = {1: 0, 2: 0, 3: 0.02, 4: 0.03, 5: 0.05, 6: 0.15, 7: 0.45, 8: 0.45, 9: 0.2, 10: 0.04, 11: 0, 12: 0}
WESTERLY_P = {1: .25, 2: .30, 3: .35, 4: .30, 5: .20, 6: .08, 7: .05, 8: .05, 9: .08, 10: .10, 11: .12, 12: .20}

CAUSES_RAIN = ["heavy rain", "flash floods", "a landslide", "a rockslide", "mudslides"]
T_BLOCK = [
    "Karakoram Highway blocked at {place} after {cause}",
    "KKH closed for traffic near {place} due to {cause}",
    "Traffic suspended on KKH at {place}; passengers stranded",
    "{Place}: landslide blocks Karakoram Highway, NHA machinery dispatched",
]
T_BLOCK_UR = ["شاہراہ قراقرم لینڈ سلائیڈنگ کے باعث ٹریفک کے لیے بند", "شاہراہ قراقرم پر ٹریفک معطل، مسافر پھنس گئے"]
T_REMAIN = ["KKH remains blocked at {place} as clearance work continues",
            "Karakoram Highway still closed near {place}, travellers stranded"]
T_OPEN = ["Karakoram Highway reopened for traffic at {place}",
          "KKH restored after debris cleared near {place}",
          "Traffic resumed on KKH at {place} after clearance"]
T_OPEN_UR = ["شاہراہ قراقرم ٹریفک کے لیے بحال کر دی گئی"]
T_PARTIAL = ["One-way traffic restored on KKH at {place}"]

# Climatology targets used for calibration (approximate, from PMD normals & news frequency)
ANNUAL_MM = {"HZR-BSM": 1300, "BSM-DSU": 1000, "DSU-CHL": 500, "CHL-JGL": 250,
             "JGL-GLT": 170, "GLT-HNZ": 220, "HNZ-SST": 280, "SST-KJB": 500}
CLOSURE_DAYS_PER_YEAR = {"HZR-BSM": 2, "BSM-DSU": 10, "DSU-CHL": 20, "CHL-JGL": 15,
                         "JGL-GLT": 3, "GLT-HNZ": 6, "HNZ-SST": 8, "SST-KJB": 6}
MEAN_SPELL_DAYS = 1.7
# How strongly closures follow the weather. Documented events (data/labels) show that
# heavy storms over Kohistan/Diamer block the road almost every time, while dry days
# almost never do, so the hazard is steep. Annual closure counts stay fixed by the
# intercept solve below; only the dependence on weather gets sharper.
HAZARD_SHARPNESS = 2.2


def _solve_intercept(z_core: np.ndarray, target_rate: float) -> float:
    lo, hi = -25.0, 10.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if (1 / (1 + np.exp(-(z_core + mid)))).mean() > target_rate:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def _storm_series(dates: pd.DatetimeIndex, probs: dict, rng, persist: float = 0.55):
    """Regional storm on/off with persistence + a shared intensity multiplier."""
    on = np.zeros(len(dates), bool)
    for i, d in enumerate(dates):
        p = probs[d.month]
        if i and on[i - 1]:
            p = min(0.95, p + persist)
        on[i] = rng.random() < p
    intensity = rng.lognormal(0, 0.55, len(dates)) * on
    return on, intensity


def simulate(start="2015-01-01", end=None, seed=42):
    rng = np.random.default_rng(seed)
    end = end or (pd.Timestamp.today().normalize() - pd.Timedelta(days=1))
    dates = pd.date_range(start, end, freq="D")
    n = len(dates)
    doy = dates.dayofyear.values
    year_mult = {y: rng.lognormal(0, 0.25) for y in dates.year.unique()}
    if 2022 in year_mult:            # 2022 super-monsoon
        year_mult[2022] *= 1.8
    ym = np.array([year_mult[y] for y in dates.year])

    m_on, m_int = _storm_series(dates, MONSOON_P, rng, persist=0.35)
    w_on, w_int = _storm_series(dates, WESTERLY_P, rng, persist=0.30)
    heat = np.zeros(n)
    for i in range(1, n):             # summer heatwave anomaly process
        shock = rng.random() < (0.02 if dates[i].month in (5, 6, 7, 8) else 0.002)
        heat[i] = 0.85 * heat[i - 1] + (rng.uniform(4, 8) if shock else 0)

    weather, truth, news, events = [], [], [], []
    for seg in SEGMENTS:
        mon_exp = float(np.clip((36.9 - seg.lat) / 2.3, 0.2, 1.0))
        elev = seg.elevation_m
        # --- precipitation
        p_m = np.where(m_on & (rng.random(n) < 0.8), rng.gamma(0.9, 14 * mon_exp, n) * m_int, 0)
        p_w = np.where(w_on & (rng.random(n) < 0.8), rng.gamma(0.9, 10, n) * w_int, 0)
        conv = np.where((dates.month.isin([5, 6, 7, 8, 9])) & (rng.random(n) < 0.08), rng.gamma(1, 4, n), 0)
        raw = (p_m + p_w + conv) * ym
        years = n / 365.25
        precip = np.round(raw * ANNUAL_MM[seg.id] / (raw.sum() / years), 1)
        # --- temperature
        amp = 11 + 2 * (elev - 900) / 3000
        tmean_clim = 21 - 6.5 * (elev - 900) / 1000 - amp * np.cos(2 * np.pi * (doy - 15) / 365)
        anom = np.zeros(n)
        for i in range(1, n):
            anom[i] = 0.8 * anom[i - 1] + rng.normal(0, 1.4)
        wet = precip > 1
        tmean = tmean_clim + anom + heat - 2.0 * wet
        rng_dt = np.where(wet, 7, 12) + rng.normal(0, 1.2, n)
        tmax, tmin = np.round(tmean + rng_dt / 2, 1), np.round(tmean - rng_dt / 2, 1)
        snow_frac = np.clip((1.5 - tmean) / 3, 0, 1)
        snow = np.round(precip * snow_frac * 0.7, 1)
        phours = np.where(precip > 0, np.clip(2 + precip * 0.6 + rng.normal(0, 2, n), 1, 24), 0).round(0)
        wind = np.round(9 + 0.002 * elev + 7 * (m_on | w_on) + rng.gamma(2, 2.5, n), 1)
        weather.append(pd.DataFrame({
            "date": dates, "segment_id": seg.id, "temperature_2m_max": tmax,
            "temperature_2m_min": tmin, "precipitation_sum": precip, "snowfall_sum": snow,
            "precipitation_hours": phours, "wind_speed_10m_max": wind}))

        # --- closure hazard
        rel = precip / (ANNUAL_MM[seg.id] / 365.25)      # rain relative to local climate
        s = pd.Series(rel)
        rain3_prev = s.shift(1).rolling(3, min_periods=1).sum().fillna(0).values
        rain10_prev = s.shift(1).rolling(10, min_periods=1).sum().fillna(0).values
        freeze_thaw = ((tmin < 0) & (tmax > 2) & (rain10_prev > 8)).astype(float)
        heat_thr = 33 - 6.5 * (elev - 900) / 1000
        melt = np.clip(tmax - heat_thr, 0, None) * (1.0 if seg.glacier_fed else 0.0)
        spring = np.isin(dates.month, [3, 4]).astype(float)
        z_core = (1.1 * np.log1p(rel) + 0.6 * np.log1p(rain3_prev / 3) + 0.3 * np.log1p(rain10_prev / 10)
                  + 0.45 * np.log1p(snow) + 0.8 * freeze_thaw + 0.35 * melt + 0.3 * spring)
        z_core = HAZARD_SHARPNESS * z_core
        open_season = np.array([not in_seasonal_closure(seg, d) for d in dates])
        target = CLOSURE_DAYS_PER_YEAR[seg.id] / MEAN_SPELL_DAYS / (365.25 * open_season.mean())
        z = z_core + _solve_intercept(z_core[open_season], target)
        p_onset = 1 / (1 + np.exp(-z))

        closed = np.zeros(n, int)
        cause = np.array([""] * n, dtype=object)
        i = 0
        while i < n:
            if in_seasonal_closure(seg, dates[i]):
                closed[i], cause[i] = 1, "seasonal"
                i += 1
                continue
            if rng.random() < p_onset[i]:
                pc = float(np.clip(0.75 - 0.08 * np.log1p(rel[i] + snow[i]), 0.3, 0.85))
                dur = int(rng.geometric(pc))
                c = ("glacial flood" if melt[i] > 1 and precip[i] < 5 else
                     "snowfall" if snow[i] > 3 else rng.choice(CAUSES_RAIN))
                closed[i:i + dur], cause[i:i + dur] = 1, c
                events.append({"date": dates[i], "segment_id": seg.id, "source": "simulated", "detail": c})
                i += dur
                continue
            if rng.random() < 0.0008:           # accidents / protests
                closed[i], cause[i] = 1, "accident"
            i += 1
        truth.append(pd.DataFrame({"date": dates, "segment_id": seg.id, "closed": closed, "cause": cause}))

        # --- news generation (noisy, incomplete — like reality)
        places = list(seg.places)
        for i in range(n):
            if cause[i] == "seasonal":
                continue
            place = str(rng.choice(places)).title()
            prev = closed[i - 1] if i else 0
            gen = rng.random() < 0.3   # generic KKH report without a place name
            if closed[i] and not prev and rng.random() < 0.8:
                if gen:
                    t = rng.choice(T_BLOCK_UR)
                else:
                    t = rng.choice(T_BLOCK).format(place=place, Place=place, cause=cause[i])
                news.append((dates[i], seg.id, t))
            elif closed[i] and prev and rng.random() < 0.45:
                news.append((dates[i], seg.id, rng.choice(T_REMAIN).format(place=place)))
            elif not closed[i] and prev and cause[i - 1] != "seasonal" and rng.random() < 0.55:
                t = rng.choice(T_OPEN_UR) if gen else rng.choice(T_OPEN + T_PARTIAL).format(place=place)
                news.append((dates[i], seg.id, t))
            elif not closed[i] and rng.random() < 0.002:   # rumours / false alarms
                news.append((dates[i], seg.id, rng.choice(T_BLOCK).format(place=place, Place=place,
                                                                              cause="a landslide")))

    news_df = pd.DataFrame(news, columns=["date", "true_segment", "title"])
    return (pd.concat(weather, ignore_index=True), pd.concat(truth, ignore_index=True),
            news_df, pd.DataFrame(events))
