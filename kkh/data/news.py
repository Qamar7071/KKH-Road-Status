"""
News + social signal collector and classifier.

1. `fetch_news()` pulls RSS feeds (Google News EN/UR queries + Pamir Times), no API keys.
2. `classify_text()` turns a headline/summary into
       {status: blocked|open|partial|none, segments: [...], kkh: bool, confidence}
   using an English + Urdu keyword lexicon and the place-name gazetteer in config.
3. Every fetched article is appended to data/raw/news_archive.jsonl, so the archive
   grows daily (via the scheduled job) and becomes labelled training data over time.
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import requests

from kkh.config import NEWS_FEEDS, RAW_DIR, SEGMENTS, USER_AGENT

ARCHIVE = RAW_DIR / "news_archive.jsonl"

KKH_PAT = re.compile(r"karakoram highway|\bkkh\b|\bn-?35\b|شاہراہ\s*قراقرم|قراقرم\s*ہائی\s*وے", re.I)

# (pattern, weight). Phrases are matched on lower-cased text.
BLOCK_TERMS = [
    (r"\bblock(ed|ing|age)?\b", 1.0), (r"\bclos(ed|ure)\b", 1.0), (r"\bsuspend(ed)?\b", 1.0),
    (r"traffic (was )?(halted|suspended|stopped)", 1.2), (r"\bimpassable\b", 1.2),
    (r"\bstranded\b", 0.8), (r"washed away|swept away|caved in|collapsed", 1.2),
    (r"\bcut off\b", 1.0), (r"landslid(e|es|ing)|rockslide|rock ?fall|mudslide", 0.7),
    (r"flash flood", 0.6), (r"\bglof\b|glacial (lake )?outburst", 0.6),
    (r"بند", 1.0), (r"معطل", 1.0), (r"لینڈ\s*سلائیڈ", 0.7), (r"پھنس", 0.8), (r"سیلاب", 0.5),
]
OPEN_TERMS = [
    (r"\bre-?open(ed|s|ing)?\b", 1.6), (r"\brestor(ed|ation of traffic)\b", 1.6),
    (r"\bcleared\b", 1.3), (r"open(ed)? for (all )?traffic", 1.6), (r"traffic resumed|resum(ed|es)", 1.4),
    (r"بحال", 1.6), (r"کھول\s*دی", 1.6),
]
PARTIAL_TERMS = [(r"one-?way traffic|partially (open|restored)|light vehicles only", 1.0),
                 (r"یکطرفہ", 1.0)]

# Pre-compile place patterns -> segment id
_PLACE_PATTERNS = [
    (re.compile(r"\b" + re.escape(p) + r"\b", re.I), seg.id)
    for seg in SEGMENTS for p in seg.places
]


def _score(text: str, terms) -> float:
    return sum(w for pat, w in terms if re.search(pat, text, re.I))


def find_segments(text: str) -> list[str]:
    hits = []
    for pat, sid in _PLACE_PATTERNS:
        if pat.search(text) and sid not in hits:
            hits.append(sid)
    return hits


def classify_text(text: str) -> dict:
    t = unescape(text or "").lower()
    kkh = bool(KKH_PAT.search(t))
    segs = find_segments(t)
    b, o, p = _score(t, BLOCK_TERMS), _score(t, OPEN_TERMS), _score(t, PARTIAL_TERMS)

    if not kkh and not segs:
        return {"status": "none", "segments": [], "kkh": False, "confidence": 0.0}
    if p and o >= b:
        status = "partial"
    elif o >= 1.3 and o >= b * 0.9:      # "reopened after being blocked" -> open
        status = "open"
    elif b >= 1.0:
        status = "blocked"
    else:
        status = "none"
    road_context = kkh or bool(re.search(r"highway|road|traffic|سڑک|شاہراہ", t))
    if not road_context:
        status = "none"
    conf = min(1.0, 0.35 + 0.15 * max(b, o) + (0.2 if kkh else 0) + (0.1 if segs else 0))
    return {"status": status, "segments": segs, "kkh": kkh, "confidence": round(conf, 2)}


# ---------------------------------------------------------------- fetching
def _parse_rss(xml_text: str, source: str) -> list[dict]:
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate")
        try:
            published = parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else None
        except (TypeError, ValueError):
            published = None
        out.append({
            "id": hashlib.md5((link or title).encode()).hexdigest()[:16],
            "title": unescape(title), "summary": unescape(desc)[:500].strip(),
            "link": link, "source": source,
            "published": (published or datetime.now(timezone.utc)).isoformat(),
        })
    return out


def fetch_news(timeout: int = 12) -> list[dict]:
    """Fetch all feeds, classify, append new items to the archive, return classified items."""
    items: list[dict] = []
    for url in NEWS_FEEDS:
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            items += _parse_rss(r.text, source=url.split("/")[2])
        except requests.RequestException:
            continue
    for it in items:
        it.update(classify_text(f"{it['title']}. {it['summary']}"))
    _append_archive(items)
    return [i for i in items if i["status"] != "none"]


def _append_archive(items: list[dict]) -> None:
    seen = set()
    if ARCHIVE.exists():
        with ARCHIVE.open(encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["id"])
                except (json.JSONDecodeError, KeyError):
                    pass
    with ARCHIVE.open("a", encoding="utf-8") as f:
        for it in items:
            if it["id"] not in seen:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")


def load_archive() -> list[dict]:
    if not ARCHIVE.exists():
        return []
    out = []
    with ARCHIVE.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec.update(classify_text(f"{rec.get('title','')}. {rec.get('summary','')}"))
            out.append(rec)
    return out
