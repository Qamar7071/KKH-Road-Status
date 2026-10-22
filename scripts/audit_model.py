"""
Model audit: hunt for silent bugs before trusting the numbers.

    python scripts/audit_model.py            # prints findings, writes models/audit.json

Checks
  1 data integrity        gaps, duplicates, NaNs, impossible values, label coverage
  2 rolling backtest      train on past years only, test each year 2019-2026
  3 calibration choice    isotonic vs sigmoid vs raw; ties and resolution
  4 forecast optimism     perfect next-day weather vs realistic forecast error
  5 monotonicity          more rain / snow / reports should never lower risk
  6 error analysis        misses and false alarms by segment, month and cause
  7 train/serve skew      replay features must equal the training features
  8 edge cases            seasonal rule, probability floor, segment ties
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.isotonic import IsotonicRegression  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import average_precision_score as PR, brier_score_loss as BR, roc_auc_score as AUC  # noqa: E402

from kkh.config import MODEL_DIR, PROCESSED_DIR, SEGMENT_IDS  # noqa: E402
from kkh.features import FEATURES, add_forecast_noise, build_features  # noqa: E402
from kkh.model import MODEL_PATH, KKHModel, load_training_frame  # noqa: E402

OUT = {}


def head(t):
    print(f"\n{'=' * 8} {t} {'=' * (60 - len(t))}")


def rec(y, p, thr=0.2):
    flag = p >= thr
    return float((flag & (y == 1)).sum() / max(1, (y == 1).sum())), float((flag & (y == 1)).sum() / max(1, flag.sum()))


# ------------------------------------------------------------------ 1
def data_integrity():
    head("1. Data integrity")
    rd = lambda f: pd.read_csv(PROCESSED_DIR / f, parse_dates=["date"])
    w, lab = rd("weather.csv"), rd("labels.csv")
    res = {}
    for name, df in (("weather", w), ("labels", lab)):
        dup = int(df.duplicated(["date", "segment_id"]).sum())
        gaps = {}
        for sid, g in df.groupby("segment_id"):
            d = g.date.sort_values().diff().dt.days.dropna()
            gaps[sid] = int((d != 1).sum())
        res[name] = {"rows": len(df), "duplicates": dup, "gaps": sum(gaps.values()), "nans": int(df.isna().sum().sum())}
    bad = {"neg_precip": int((w.precipitation_sum < 0).sum()), "neg_snow": int((w.snowfall_sum < 0).sum()),
           "tmin_gt_tmax": int((w.temperature_2m_min > w.temperature_2m_max).sum()),
           "precip_gt_300": int((w.precipitation_sum > 300).sum()), "snow_gt_precip": int((w.snowfall_sum > w.precipitation_sum + 0.05).sum())}
    res["impossible_values"] = bad
    print(json.dumps(res, indent=1))
    OUT["data_integrity"] = res


# ------------------------------------------------------------------ 2
def backtest(X):
    head("2. Rolling backtest (train on the past only)")
    rows = []
    for Y in range(2019, 2027):
        tr = X[X.date.dt.year <= Y - 2]; ca = X[X.date.dt.year == Y - 1]; te = X[X.date.dt.year == Y]
        if te.target.sum() < 5:
            continue
        m = KKHModel().fit(add_forecast_noise(tr, 1), add_forecast_noise(ca, 2))
        ten = add_forecast_noise(te, 3)
        p = m.predict_proba(ten); y = ten.target.values
        r, pr = rec(y, p)
        rows.append({"year": Y, "test_rows": len(te), "blocked": int(y.sum()), "auc": round(AUC(y, p), 3),
                     "pr_auc": round(PR(y, p), 3), "brier": round(BR(y, p), 4), "recall@.2": round(r, 2), "prec@.2": round(pr, 2)})
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print(f"AUC mean {df.auc.mean():.3f} ± {df.auc.std():.3f}; PR-AUC mean {df.pr_auc.mean():.3f} ± {df.pr_auc.std():.3f}")
    OUT["backtest"] = rows


# ------------------------------------------------------------------ 3
def calibration(X):
    head("3. Calibration method")
    tr = add_forecast_noise(X[X.date <= "2022-12-31"], 1)
    ca = add_forecast_noise(X[(X.date > "2022-12-31") & (X.date <= "2023-12-31")], 2)
    te = add_forecast_noise(X[X.date > "2023-12-31"], 3)
    base = KKHModel().fit(tr, ca)
    r_ca, r_te, y_ca, y_te = base.raw(ca), base.raw(te), ca.target.values, te.target.values
    out = {}
    cands = {
        "raw_hgb": 1 / (1 + np.exp(-r_te)),
        "isotonic": IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999).fit(r_ca, y_ca).predict(r_te),
        "sigmoid (in use)": base.predict_proba(te),
    }
    for k, p in cands.items():
        bins = pd.cut(p, [0, .05, .1, .2, .35, .5, 1.01], right=False)
        cal = pd.DataFrame({"p": p, "y": y_te, "b": bins}).groupby("b", observed=True).agg(n=("y", "size"), pred=("p", "mean"), obs=("y", "mean"))
        ece = float((abs(cal.pred - cal.obs) * cal.n).sum() / cal.n.sum())
        out[k] = {"auc": round(AUC(y_te, p), 4), "pr_auc": round(PR(y_te, p), 4), "brier": round(BR(y_te, p), 5),
                  "ece": round(ece, 4), "unique_values": int(len(np.unique(np.round(p, 5))))}
    print(pd.DataFrame(out).T.to_string())
    print(f"calibration year positives: {int(y_ca.sum())} of {len(y_ca)}")
    OUT["calibration"] = out


# ------------------------------------------------------------------ 4
def optimism(X, model):
    head("4. Perfect weather vs realistic forecast (test 2024+)")
    te = X[X.date > "2023-12-31"]
    y = te.target.values
    res = {}
    for name, frame in (("perfect_next_day_weather", te), ("realistic_forecast", add_forecast_noise(te, 3))):
        p = model.predict_proba(frame); r, pr = rec(y, p)
        res[name] = {"auc": round(AUC(y, p), 3), "pr_auc": round(PR(y, p), 3), "recall@.2": round(r, 2), "prec@.2": round(pr, 2)}
    print(pd.DataFrame(res).T.to_string())
    OUT["optimism"] = res


# ------------------------------------------------------------------ 5
def monotonicity(X, model):
    head("5. Monotonicity sweeps (share of rows where MORE x LOWERS risk by >2 pts)")
    rng = np.random.default_rng(0)
    sweeps = {"f_precip": np.linspace(0, 120, 13), "f_snow": np.linspace(0, 60, 13),
              "rain7": np.linspace(0, 300, 13), "news_block_t": np.arange(0, 5)}
    res = {}
    for sid in SEGMENT_IDS:
        rows = X[X.segment_id == sid]
        sample = rows.iloc[rng.choice(len(rows), 120, replace=False)]
        res[sid] = {}
        for f, grid in sweeps.items():
            viol, worst = 0, 0.0
            for _, r in sample.iterrows():
                frame = pd.DataFrame([r] * len(grid))
                frame[f] = grid
                if f == "f_precip":
                    frame["storm_total"] = frame["precip_t"] + grid
                if f == "rain7":
                    frame["rain14"] = np.maximum(frame["rain14"], grid); frame["rain30"] = np.maximum(frame["rain30"], frame["rain14"])
                if f == "news_block_t":
                    frame["news_block_3d"] = np.maximum(frame["news_block_3d"], grid)
                p = model.predict_proba(frame)
                drop = np.maximum.accumulate(p) - p
                if drop.max() > 0.02:
                    viol += 1; worst = max(worst, float(drop.max()))
            res[sid][f] = {"violations_pct": round(100 * viol / len(sample), 1), "worst_drop_pts": round(100 * worst, 1)}
    df = pd.DataFrame({sid: {f: f"{v['violations_pct']}% (max -{v['worst_drop_pts']})" for f, v in d.items()} for sid, d in res.items()}).T
    print(df.to_string())
    OUT["monotonicity"] = res


# ------------------------------------------------------------------ 6
def errors(X, model):
    head("6. Where it goes wrong (test 2024+, realistic forecast, threshold 20%)")
    te = add_forecast_noise(X[X.date > "2023-12-31"], 3).copy()
    te["p"] = model.predict_proba(te)
    lab = pd.read_csv(PROCESSED_DIR / "labels.csv", parse_dates=["date"])
    lab = lab.assign(cause=lab.get("cause", "?"))[["date", "segment_id", "cause"]]
    lab["date"] = lab["date"] - pd.Timedelta(days=1)
    te = te.merge(lab, on=["date", "segment_id"], how="left")
    te["flag"] = te.p >= 0.2
    te["onset"] = (te.target == 1) & (te.news_block_t == 0) & (te.news_block_3d == 0)
    by = lambda col: te[te.target == 1].groupby(col).agg(blocked=("target", "size"), caught=("flag", "mean")).round(2)
    seg = by("segment_id"); mon = by(te.date.dt.month.rename("month")); cause = by(te.cause.fillna("?").rename("cause"))
    fa = te[(te.target == 0) & te.flag].groupby(te.date.dt.month.rename("month")).size().rename("false_alarms")
    onset = te[te.target == 1].groupby("onset").agg(n=("target", "size"), caught=("flag", "mean")).round(2)
    print("by segment\n", seg.T.to_string()); print("by month\n", mon.T.to_string()); print("false alarms by month\n", fa.to_string())
    print("by cause\n", cause.to_string()); print("new blockage (no report yet) vs ongoing\n", onset.to_string())
    OUT["errors"] = {"by_segment": seg.to_dict(), "by_cause": cause.to_dict(), "onset": onset.to_dict(),
                     "false_alarms_by_month": fa.to_dict()}


# ------------------------------------------------------------------ 7
def skew():
    head("7. Train/serve skew: replay features vs training features")
    from kkh.predict import Predictor
    P = Predictor()
    rd = lambda f: pd.read_csv(PROCESSED_DIR / f, parse_dates=["date"])
    full = build_features(rd("weather.csv"), rd("news_daily.csv"), rd("history.csv"))
    rng = np.random.default_rng(1)
    lo, hi = P.data_range
    dates = pd.to_datetime(rng.choice(pd.date_range(lo, hi), 40, replace=False))
    diffs = {}
    for d in dates:
        res = P.replay(d)                                   # the exact code path the app uses
        a = pd.DataFrame({s["id"]: s["features"] for s in res["segments"]}).T[FEATURES]
        b = full[full.date == d].set_index("segment_id")[FEATURES].loc[a.index]
        dd = (a.astype(float) - b.astype(float)).abs().max()
        for f, v in dd.items():
            if v > 1e-4:                                    # features are rounded to 5 dp in the API
                diffs[f] = max(diffs.get(f, 0), float(v))
    print("features that differ (max abs diff):", diffs or "none")
    OUT["skew"] = diffs


# ------------------------------------------------------------------ 8
def edges(model, X):
    head("8. Edge cases")
    te = X[X.date > "2023-12-31"]
    p = model.predict_proba(te)
    res = {"min_prob": round(float(p.min()), 4), "share_at_floor": round(float((p <= p.min() + 1e-9).mean()), 3),
           "distinct_probabilities": int(len(np.unique(np.round(p, 5))))}
    print(res)
    OUT["edges"] = res


if __name__ == "__main__":
    X, _ = load_training_frame()
    model = joblib.load(MODEL_PATH)
    data_integrity(); backtest(X); calibration(X); optimism(X, model); monotonicity(X, model); errors(X, model); skew(); edges(model, X)
    (MODEL_DIR / "audit.json").write_text(json.dumps(OUT, indent=1, default=str))
