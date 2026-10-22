"""
Turn raw evidence into daily labels: closed (1) / open (0) per (date, segment).

REAL mode (weak supervision — this is the honest way to label KKH data):
  * documented_events.csv            -> closed, highest trust
  * news "blocked" report on day d   -> closed from d until the next "open" report
                                        for that segment, capped at MAX_SPELL days
  * no report                        -> assumed open (noisy negative)
  * Khunjerab winter window          -> excluded from ML (handled by a schedule rule)
"""
from __future__ import annotations

import pandas as pd

from kkh.config import SEG_BY_ID, SEGMENT_IDS, in_seasonal_closure
from kkh.data.landslides import load_documented_events

MAX_SPELL = 4


def labels_from_news(news_daily: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """news_daily: columns date, segment_id, news_block, news_open (from features.aggregate_news)."""
    rows = []
    nd = news_daily.set_index(["segment_id", "date"]).sort_index() if len(news_daily) else None
    for sid in SEGMENT_IDS:
        spell = 0
        for d in dates:
            blk = opn = 0
            if nd is not None and (sid, d) in nd.index:
                blk, opn = nd.loc[(sid, d), ["news_block", "news_open"]]
            if blk > opn:
                spell = MAX_SPELL
            elif opn > 0:
                spell = 0
            rows.append({"date": d, "segment_id": sid, "closed": int(spell > 0)})
            spell = max(0, spell - 1)
    return pd.DataFrame(rows)


def build_real_labels(news_daily: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    lab = labels_from_news(news_daily, dates)
    lab["cause"] = lab["closed"].map({1: "news report", 0: ""})
    doc = load_documented_events()
    doc = doc[doc["date"].isin(dates)]
    key = lab.set_index(["date", "segment_id"]).index
    doc_cause = {(d, s): str(c).split(":")[0] for d, s, c in zip(doc["date"], doc["segment_id"], doc["detail"])}
    hit = key.isin(list(doc_cause))
    lab.loc[hit, "closed"] = 1
    lab.loc[hit, "cause"] = [doc_cause[k] for k in key[hit]]
    lab["seasonal"] = [in_seasonal_closure(SEG_BY_ID[s], d) for d, s in zip(lab.date, lab.segment_id)]
    return lab
