"""Data, features and model: correctness checks that protect the forecast."""
import json

import joblib
import numpy as np
import pandas as pd
import pytest

from kkh.config import PROCESSED_DIR, SEG_BY_ID, SEGMENT_IDS, SEGMENTS, in_seasonal_closure, risk_band
from kkh.data.news import classify_text
from kkh.features import FEATURES, add_forecast_noise, build_features
from kkh.model import EXPORT_PATH, MODEL_PATH, REPORT_PATH, load_training_frame


# ---------------------------------------------------------------- config
def test_segments_are_contiguous_and_cover_the_road():
    for a, b in zip(SEGMENTS, SEGMENTS[1:]):
        assert a.route[-1] == b.route[0], f"{a.id} does not join {b.id}"
    assert sum(s.length_km for s in SEGMENTS) == 805


def test_khunjerab_winter_closure_wraps_new_year():
    kjb = SEG_BY_ID["SST-KJB"]
    assert in_seasonal_closure(kjb, pd.Timestamp("2026-12-15"))
    assert in_seasonal_closure(kjb, pd.Timestamp("2027-02-01"))
    assert not in_seasonal_closure(kjb, pd.Timestamp("2026-07-01"))
    assert not in_seasonal_closure(SEG_BY_ID["DSU-CHL"], pd.Timestamp("2026-12-15"))


@pytest.mark.parametrize("p,key", [(0.0, "open"), (0.19, "open"), (0.2, "caution"), (0.5, "blocked"), (1.0, "blocked")])
def test_risk_bands(p, key):
    assert risk_band(p)["key"] == key


# ---------------------------------------------------------------- news classifier
@pytest.mark.parametrize("text,status,seg", [
    ("Karakoram Highway blocked at Tatta Pani after landslide", "blocked", "CHL-JGL"),
    ("KKH restored for traffic near Lotar", "open", "DSU-CHL"),
    ("One-way traffic restored on KKH at Attabad", "partial", "HNZ-SST"),
    ("Traffic suspended on KKH at Dasu; passengers stranded", "blocked", "DSU-CHL"),
])
def test_news_classifier_english(text, status, seg):
    c = classify_text(text)
    assert c["status"] == status and seg in c["segments"]


def test_news_classifier_urdu_and_irrelevant():
    assert classify_text("شاہراہ قراقرم لینڈ سلائیڈنگ کے باعث ٹریفک کے لیے بند")["status"] == "blocked"
    assert classify_text("Cricket match in Gilgit")["status"] == "none"


# ---------------------------------------------------------------- features
@pytest.fixture(scope="module")
def frames():
    rd = lambda f: pd.read_csv(PROCESSED_DIR / f)
    return rd("weather.csv"), rd("news_daily.csv"), rd("history.csv"), rd("labels.csv")


def test_features_complete_and_finite(frames):
    X = build_features(*frames)
    assert set(FEATURES) <= set(X.columns)
    assert np.isfinite(X[FEATURES].to_numpy(float)).all()


def test_target_is_tomorrows_label(frames):
    w, nd, h, lab = frames
    X = build_features(w, nd, h, lab)
    lab = lab.assign(date=pd.to_datetime(lab.date)).set_index(["date", "segment_id"])
    sample = X.sample(200, random_state=0)
    for d, s, t in zip(sample.date, sample.segment_id, sample.target):
        assert lab.loc[(d + pd.Timedelta(days=1), s), "closed"] == t


def test_no_future_leak_in_antecedent_features(frames):
    """Changing weather AFTER tomorrow must not change today's features."""
    w, nd, h, _ = frames
    w = w.copy(); w["date"] = pd.to_datetime(w["date"])
    t = pd.Timestamp("2022-08-10")
    base = build_features(w, nd, h)
    w2 = w.copy()
    w2.loc[w2.date > t + pd.Timedelta(days=1), ["precipitation_sum", "snowfall_sum"]] = 999.0
    alt = build_features(w2, nd, h)
    a = base[base.date == t].set_index("segment_id")[FEATURES]
    b = alt[alt.date == t].set_index("segment_id")[FEATURES]
    pd.testing.assert_frame_equal(a, b)


def test_forecast_noise_only_touches_forecast_inputs(frames):
    X = build_features(*frames).head(3000)
    Xn = add_forecast_noise(X, seed=1)
    untouched = [f for f in FEATURES if not f.startswith("f_") and f != "storm_total"]
    pd.testing.assert_frame_equal(X[untouched], Xn[untouched])
    assert not X["f_precip"].equals(Xn["f_precip"])


# ---------------------------------------------------------------- model
@pytest.fixture(scope="module")
def model():
    return joblib.load(MODEL_PATH)


def test_model_beats_season_baseline():
    rep = json.loads(REPORT_PATH.read_text())["test"]
    assert rep["model"]["roc_auc"] > rep["season_baseline"]["roc_auc"] + 0.1
    assert rep["model"]["brier"] < rep["season_baseline"]["brier"]


def test_probabilities_valid_and_monotone_in_rain(model):
    X, _ = load_training_frame()
    row = X[(X.segment_id == "DSU-CHL") & (X.date.dt.month == 7)].iloc[[0]]
    ps = []
    for mm in (0, 20, 60, 120):
        r = row.copy(); r["f_precip"] = mm; r["storm_total"] = r["precip_t"] + mm
        ps.append(float(model.predict_proba(r)[0]))
    assert all(0 <= p <= 1 for p in ps)
    assert ps[-1] > ps[0], f"more rain should not lower risk: {ps}"


def test_browser_export_matches_python(model):
    E = json.loads(EXPORT_PATH.read_text())
    assert E["features"] == FEATURES
    X, _ = load_training_frame()
    S = add_forecast_noise(X.sample(400, random_state=3), 9)

    def raw(row):
        s = E["baseline"]
        for t in E["trees"]:
            i = 0
            while not t["leaf"][i]:
                i = t["l"][i] if row[t["f"][i]] <= t["t"][i] else t["r"][i]
            s += t["v"][i]
        return s

    r = np.array([raw(v) for v in S[FEATURES].to_numpy(float)])
    a, b = E["platt"]
    p = 1 / (1 + np.exp(-(a * r + b)))
    assert np.abs(p - model.predict_proba(S)).max() < 1e-5


def test_monotone_in_physical_drivers(model):
    """Audit regression test: more rain/snow/reports must never lower the forecast."""
    X, _ = load_training_frame()
    rows = X.sample(150, random_state=4)
    for f, grid in {"f_precip": np.linspace(0, 120, 9), "rain7": np.linspace(0, 300, 9),
                    "f_snow": np.linspace(0, 60, 7), "news_block_t": np.arange(0, 5)}.items():
        for _, r in rows.iterrows():
            frame = pd.DataFrame([r] * len(grid)); frame[f] = grid
            p = model.predict_proba(frame)
            assert (np.diff(p) >= -1e-9).all(), f"{f} lowers risk for {r.segment_id} {r.date:%Y-%m-%d}"


def test_explanations_cover_all_groups(model):
    X, _ = load_training_frame()
    ex = model.explain(X.head(3))
    assert len(ex) == 3 and all(len(e) == 7 for e in ex)
