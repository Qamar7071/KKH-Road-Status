"""
Small SQLite store for two things that make the app better over time:

  reports      — community road reports ("blocked at Tatta Pani") from locals & drivers.
                 These are fed back into the model as news signals the same day.
  predictions  — a log of every live forecast, so real accuracy can be measured later
                 against what actually happened (the honest way to evaluate in production).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pandas as pd

from kkh.config import DB_PATH, SEGMENT_IDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    report_date TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('blocked', 'open', 'partial')),
    place TEXT,
    note TEXT,
    client TEXT
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    target_date TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    probability REAL NOT NULL,
    source TEXT NOT NULL,
    UNIQUE (as_of, segment_id, source)
);
"""


@contextmanager
def connect(path=DB_PATH):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def pk_today() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5)).date().isoformat()


def add_report(segment_id: str, status: str, place: str = "", note: str = "", client: str = "",
               path=DB_PATH) -> dict:
    if segment_id not in SEGMENT_IDS:
        raise ValueError("unknown segment")
    if status not in ("blocked", "open", "partial"):
        raise ValueError("status must be blocked/open/partial")
    rec = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "report_date": pk_today(), "segment_id": segment_id, "status": status,
           "place": (place or "")[:80], "note": (note or "")[:280], "client": client[:64]}
    with connect(path) as con:
        cur = con.execute("INSERT INTO reports (created_at, report_date, segment_id, status, place, note, client) "
                          "VALUES (:created_at, :report_date, :segment_id, :status, :place, :note, :client)", rec)
        rec["id"] = cur.lastrowid
    rec.pop("client")
    return rec


def recent_reports(hours: int = 48, path=DB_PATH) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with connect(path) as con:
        rows = con.execute("SELECT id, created_at, report_date, segment_id, status, place, note FROM reports "
                           "WHERE created_at >= ? ORDER BY created_at DESC LIMIT 100", (since,)).fetchall()
    return [dict(r) for r in rows]


def count_reports_from(client: str, minutes: int = 60, path=DB_PATH) -> int:
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with connect(path) as con:
        return con.execute("SELECT COUNT(*) FROM reports WHERE client = ? AND created_at >= ?",
                           (client, since)).fetchone()[0]


def reports_as_news_daily(days: int = 7, path=DB_PATH) -> pd.DataFrame:
    """Community reports -> the same per-(date, segment) counts the news pipeline produces."""
    cols = ["date", "segment_id", "news_block", "news_open", "news_generic_block", "news_generic_open"]
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    with connect(path) as con:
        rows = con.execute("SELECT report_date, segment_id, status FROM reports WHERE report_date >= ?",
                           (since,)).fetchall()
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["report_date"])
    df["news_block"] = (df.status == "blocked").astype(int)
    df["news_open"] = df.status.isin(["open", "partial"]).astype(int)
    df["news_generic_block"] = df["news_generic_open"] = 0
    return df[cols].groupby(["date", "segment_id"], as_index=False).sum()


def log_predictions(as_of: str, target_date: str, probs: dict[str, float], source: str, path=DB_PATH) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(path) as con:
        con.executemany("INSERT OR REPLACE INTO predictions (created_at, as_of, target_date, segment_id, "
                        "probability, source) VALUES (?, ?, ?, ?, ?, ?)",
                        [(now, as_of, target_date, s, float(p), source) for s, p in probs.items()])
