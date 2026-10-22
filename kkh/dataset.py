"""
Step 2 orchestrator: collect every source and save tidy tables to data/processed/.

    python -m kkh.dataset --mode auto        # try real data, fall back to bootstrap
    python -m kkh.dataset --mode real        # Open-Meteo + news archive + NASA + documented
    python -m kkh.dataset --mode synthetic   # offline bootstrap (see data/simulate.py)
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import pandas as pd

from kkh.config import HISTORY_START, PROCESSED_DIR, RAW_DIR, SEG_BY_ID, in_seasonal_closure
from kkh.data import landslides, labels as labels_mod, news as news_mod, simulate, weather
from kkh.features import aggregate_news


def _save(mode: str, w, nd, hist, lab, extra: dict | None = None):
    w.to_csv(PROCESSED_DIR / "weather.csv", index=False)
    nd.to_csv(PROCESSED_DIR / "news_daily.csv", index=False)
    hist.to_csv(PROCESSED_DIR / "history.csv", index=False)
    lab.to_csv(PROCESSED_DIR / "labels.csv", index=False)
    meta = {"mode": mode, "built_at": datetime.now(timezone.utc).isoformat(),
            "start": str(w["date"].min())[:10], "end": str(w["date"].max())[:10],
            "rows": int(len(lab)), "closed_days": int(lab["closed"].sum())} | (extra or {})
    (PROCESSED_DIR / "meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def _load_weather(end: str) -> pd.DataFrame:
    """Use the history saved by scripts/fetch_real_data.py when it is recent; else download."""
    cache = RAW_DIR / "weather_history.csv"
    if cache.exists():
        w = pd.read_csv(cache, parse_dates=["date"])
        if len(w) and w["date"].max() >= pd.Timestamp(end) - pd.Timedelta(days=10):
            print(f"[dataset] weather: {cache.name} ({w.date.min():%Y-%m-%d} to {w.date.max():%Y-%m-%d})")
            return w
    return weather.fetch_history(HISTORY_START, end)


def _load_news() -> pd.DataFrame:
    """RSS archive + the GDELT archive (2017 onward) written by scripts/fetch_real_data.py."""
    try:
        news_mod.fetch_news()                              # grow the RSS archive if online
    except Exception:  # noqa: BLE001
        pass
    items = news_mod.load_archive()
    gd = RAW_DIR / "gdelt_articles.jsonl"
    if gd.exists():
        seen = {i.get("link") for i in items}
        with gd.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("url") in seen or not r.get("seendate"):
                    continue
                items.append({"id": r["url"], "title": r.get("title", ""), "summary": r.get("summary", ""),
                              "link": r["url"], "source": r.get("domain", "gdelt"),
                              "published": datetime.strptime(r["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()})
        print(f"[dataset] news: {len(items)} articles (RSS archive + GDELT)")
    arch = pd.DataFrame(items)
    if len(arch):
        arch["date"] = pd.to_datetime(arch["published"], utc=True).dt.tz_convert("Asia/Karachi") \
            .dt.tz_localize(None).dt.normalize()
    return arch


def build_real() -> dict:
    end = (pd.Timestamp.today().normalize() - pd.Timedelta(days=2)).date().isoformat()
    w = _load_weather(end)
    arch = _load_news()
    nd = aggregate_news(arch)
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(w["date"]).unique()))
    lab = labels_mod.build_real_labels(nd, dates)
    news_events = lab[(lab.closed == 1) & (~lab.seasonal)][["date", "segment_id"]].assign(source="news")
    hist = landslides.build_history(extra=news_events)
    return _save("real", w, nd, hist, lab, {"news_articles": int(len(arch)),
                                            "news_blocked_reports": int(nd["news_block"].sum()) if len(nd) else 0})


def build_synthetic(seed: int = 42) -> dict:
    w, truth, news_df, events = simulate.simulate(HISTORY_START, seed=seed)
    nd = aggregate_news(news_df)
    truth["seasonal"] = [in_seasonal_closure(SEG_BY_ID[s], d) for d, s in zip(truth.date, truth.segment_id)]
    hist = landslides.build_history(extra=events, use_nasa=False)
    return _save("synthetic", w, nd, hist, truth[["date", "segment_id", "closed", "seasonal", "cause"]],
                 {"news_articles": int(len(news_df))})


def build(mode: str = "auto") -> dict:
    if mode in ("real", "auto"):
        try:
            return build_real()
        except Exception as e:  # noqa: BLE001 — any network/source failure -> fallback
            if mode == "real":
                raise
            print(f"[dataset] real data unavailable ({e.__class__.__name__}: {e}); using bootstrap")
    return build_synthetic()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "real", "synthetic"], default="auto")
    print(json.dumps(build(ap.parse_args().mode), indent=2))
