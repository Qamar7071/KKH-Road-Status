"""
Step 4 — train, calibrate and evaluate the "blocked tomorrow?" model.

    python -m kkh.model            # train on data/processed/*, write models/

Design choices (and why):
  * Time-based split (train 2015-22, calibrate 2023, test 2024+): a random split would
    leak tomorrow's storm into training and inflate scores.
  * HistGradientBoosting: handles non-linear thresholds (e.g. "rain7 > 40 mm AND
    freeze-thaw") with zero extra dependencies, so deployment stays light.
  * Monotonic constraints from physics: more rain, snow, melt, reports or recent slides can
    never LOWER the risk (an audit found the unconstrained model did this on ~27 % of
    Dasu-Chilas days for 7-day rain). See scripts/audit_model.py.
  * Platt (sigmoid) calibration on a held-out year: when the app says 30 %, roughly 30 % of
    such days should really be blocked. Isotonic was tried first; with ~76 positives in the
    calibration year it collapsed predictions onto 58 steps and cost PR-AUC, so sigmoid won.
  * Fixed Pak-China winter closure (Khunjerab, Dec-Mar) is a rule, not a prediction,
    so those rows are excluded from training and handled in predict.py.
  * Compared against two baselines; the model has to beat "just use the season".
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, brier_score_loss, log_loss,
                             precision_recall_fscore_support, roc_auc_score)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from kkh.config import MODEL_DIR, PROCESSED_DIR, SEGMENT_IDS
from kkh.features import FEATURE_GROUPS, FEATURES, add_forecast_noise, build_features

TRAIN_END, VALID_END = "2022-12-31", "2023-12-31"
MODEL_PATH = MODEL_DIR / "model.joblib"
REPORT_PATH = MODEL_DIR / "report.json"
EXPORT_PATH = MODEL_DIR / "model_export.json"


def load_training_frame() -> tuple[pd.DataFrame, dict]:
    rd = lambda f: pd.read_csv(PROCESSED_DIR / f)
    X = build_features(rd("weather.csv"), rd("news_daily.csv"), rd("history.csv"), rd("labels.csv"))
    X = X[~X["seasonal_next"].astype(bool)].dropna(subset=FEATURES + ["target"])
    meta = json.loads((PROCESSED_DIR / "meta.json").read_text())
    return X.reset_index(drop=True), meta


# +1: risk can only rise with this feature, -1: only fall. Everything else is unconstrained.
MONOTONE = {f: 1 for f in ["f_precip", "f_snow", "f_phours", "storm_total", "precip_t", "rain3", "rain7", "rain14",
                           "rain30", "snow7", "snow30", "f_freeze_thaw", "ft_days7", "f_melt_excess", "melt3",
                           "news_block_t", "news_block_3d", "news_generic_block_t", "news_net", "hist_365", "hist_90",
                           "susceptibility"]} | {"days_since_report": -1, "days_since_event": -1, "news_open_t": -1}


class KKHModel:
    """Monotone-constrained gradient boosting + Platt calibration, with group-level explanations."""

    def __init__(self, **hgb):
        params = dict(learning_rate=0.05, max_iter=500, max_leaf_nodes=12, min_samples_leaf=120,
                      l2_regularization=1.0, early_stopping=True, validation_fraction=0.15,
                      n_iter_no_change=30, random_state=7)
        params["monotonic_cst"] = {f: MONOTONE.get(f, 0) for f in FEATURES}
        self.clf = HistGradientBoostingClassifier(**(params | hgb))
        self.platt = (1.0, 0.0)          # p = sigmoid(a * raw + b)
        self.reference: dict[str, float] = {}

    def raw(self, X: pd.DataFrame) -> np.ndarray:
        return self.clf.decision_function(X[FEATURES])

    def fit(self, Xtr: pd.DataFrame, Xcal: pd.DataFrame) -> "KKHModel":
        self.clf.fit(Xtr[FEATURES], Xtr["target"])
        lr = LogisticRegression(C=1e6, max_iter=1000).fit(self.raw(Xcal).reshape(-1, 1), Xcal["target"])
        self.platt = (float(lr.coef_[0, 0]), float(lr.intercept_[0]))
        # "Quiet day" reference used for explanations: median of open, dry days
        quiet = Xtr[(Xtr.target == 0)]
        self.reference = {f: float(quiet[f].median()) for f in FEATURES}
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        a, b = self.platt
        return 1 / (1 + np.exp(-(a * self.raw(X) + b)))

    def explain(self, X: pd.DataFrame) -> list[dict[str, float]]:
        """Per row: how much each feature group pushes risk up (+) or down (-), in logit units.

        Method: swap one group at a time to its 'quiet day' value and measure the change
        in the model's raw score (an ablation; cheap and model-agnostic)."""
        base = self.raw(X)
        out = [dict() for _ in range(len(X))]
        for name, cols in FEATURE_GROUPS.items():
            Xa = X.copy()
            for c in cols:
                Xa[c] = self.reference[c]
            delta = base - self.raw(Xa)
            for i, d in enumerate(delta):
                out[i][name] = round(float(d), 3)
        return out


# ----------------------------------------------------------------- evaluation helpers
def _metrics(y: np.ndarray, p: np.ndarray) -> dict:
    out = {"roc_auc": round(roc_auc_score(y, p), 4), "pr_auc": round(average_precision_score(y, p), 4),
           "brier": round(brier_score_loss(y, p), 5), "log_loss": round(log_loss(y, np.clip(p, 1e-4, 1 - 1e-4)), 4),
           "base_rate": round(float(y.mean()), 4)}
    for thr in (0.2, 0.5):     # 0.2 = app's "caution" band, 0.5 = "likely blocked"
        pr, rc, f1, _ = precision_recall_fscore_support(y, p >= thr, average="binary", zero_division=0)
        out |= {f"precision@{thr}": round(pr, 3), f"recall@{thr}": round(rc, 3), f"f1@{thr}": round(f1, 3)}
    return out


def _reliability(y, p, bins=(0, .05, .1, .2, .35, .5, .7, 1.0001)):
    df = pd.DataFrame({"y": y, "p": p, "b": pd.cut(p, bins, right=False)})
    g = df.groupby("b", observed=True).agg(n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean"))
    return [{"bin": str(i), "n": int(r.n), "predicted": round(r.predicted, 3), "observed": round(r.observed, 3)}
            for i, r in g.iterrows()]


def _climatology(Xtr: pd.DataFrame, Xte: pd.DataFrame) -> np.ndarray:
    m = Xtr.assign(mo=Xtr.date.dt.month).groupby(["segment_id", "mo"]).target.mean()
    keys = list(zip(Xte.segment_id, Xte.date.dt.month))
    return np.array([m.get(k, Xtr.target.mean()) for k in keys])


# ----------------------------------------------------------------- browser export
def export_for_browser(model: KKHModel, path=EXPORT_PATH) -> None:
    """Dump trees + calibration to JSON so the same model can run in plain JavaScript."""
    trees = []
    for it in model.clf._predictors:
        n = it[0].nodes
        trees.append({"f": n["feature_idx"].astype(int).tolist(),
                      "t": np.nan_to_num(n["num_threshold"].astype(float), posinf=1e300, neginf=-1e300).tolist(),
                      "l": n["left"].astype(int).tolist(), "r": n["right"].astype(int).tolist(),
                      "leaf": n["is_leaf"].astype(int).tolist(), "v": np.round(n["value"], 9).tolist()})
    base = float(np.ravel(model.clf._baseline_prediction)[0])
    payload = {"features": FEATURES, "groups": FEATURE_GROUPS, "baseline": base, "trees": trees,
               "platt": list(model.platt), "reference": model.reference}
    path.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False))   # browser-safe JSON


# ----------------------------------------------------------------- main
def train() -> dict:
    X, meta = load_training_frame()
    tr = X[X.date <= TRAIN_END]
    ca = X[(X.date > TRAIN_END) & (X.date <= VALID_END)]
    te = X[X.date > VALID_END]
    # Live inputs are forecasts, so every split sees forecast-like noise (different seeds)
    tr_n, ca_n, te_n = add_forecast_noise(tr, 1), add_forecast_noise(ca, 2), add_forecast_noise(te, 3)

    model = KKHModel().fit(tr_n, ca_n)
    y = te_n["target"].values
    p_model = model.predict_proba(te_n)

    logit = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5))
    logit.fit(tr_n[FEATURES], tr_n["target"])
    p_logit = logit.predict_proba(te_n[FEATURES])[:, 1]
    p_clim = _climatology(tr_n, te_n)

    per_seg = {}
    for sid in SEGMENT_IDS:
        m = (te_n.segment_id == sid).values
        if m.sum() and 0 < y[m].sum() < m.sum():
            per_seg[sid] = {"n": int(m.sum()), "blocked_days": int(y[m].sum()),
                            "roc_auc": round(roc_auc_score(y[m], p_model[m]), 3),
                            "pr_auc": round(average_precision_score(y[m], p_model[m]), 3)}

    # Global importance = mean |group impact| on blocked test days
    sample = te_n[te_n.target == 1].sample(min(400, int(te_n.target.sum())), random_state=0)
    ex = pd.DataFrame(model.explain(sample))
    importance = ex.abs().mean().sort_values(ascending=False).round(3).to_dict()

    report = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "data_mode": meta.get("mode"),
        "split": {"train": f"{tr.date.min():%Y-%m-%d} → {tr.date.max():%Y-%m-%d}",
                  "calibration": f"{ca.date.min():%Y-%m-%d} → {ca.date.max():%Y-%m-%d}",
                  "test": f"{te.date.min():%Y-%m-%d} → {te.date.max():%Y-%m-%d}",
                  "rows": {"train": len(tr), "calibration": len(ca), "test": len(te)}},
        "n_trees": int(model.clf.n_iter_),
        "test": {"model": _metrics(y, p_model), "logistic_baseline": _metrics(y, p_logit),
                 "season_baseline": _metrics(y, p_clim)},
        "per_segment": per_seg,
        "reliability": _reliability(y, p_model),
        "group_importance": importance,
    }
    joblib.dump(model, MODEL_PATH)
    export_for_browser(model)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


if __name__ == "__main__":
    # import via the package so the pickled class path is kkh.model.KKHModel, not __main__
    from kkh import model as _m
    r = _m.train()
    t = r["test"]
    print(f"data={r['data_mode']}  trees={r['n_trees']}  test={r['split']['test']}")
    print(f"{'':20}{'ROC-AUC':>9}{'PR-AUC':>9}{'Brier':>9}{'Rec@.2':>8}{'Prec@.2':>8}")
    for k, m in t.items():
        print(f"{k:20}{m['roc_auc']:>9}{m['pr_auc']:>9}{m['brier']:>9}{m['recall@0.2']:>8}{m['precision@0.2']:>8}")
    print("per segment:", {k: v["roc_auc"] for k, v in r["per_segment"].items()})
    print("reliability:", [(b["predicted"], b["observed"], b["n"]) for b in r["reliability"]])
    print("drivers:", r["group_importance"])
