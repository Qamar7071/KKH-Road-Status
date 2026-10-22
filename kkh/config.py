"""
Central configuration for the KKH Road Status Predictor.

The Karakoram Highway (N-35) is split into 8 operational segments. Each segment
has one representative weather point (used for Open-Meteo), a route polyline for
the map, a gazetteer of place names (used to map news text -> segment), and a
prior landslide susceptibility derived from terrain + documented history.
Coordinates are approximate (good to ~5 km), which is fine for 9-11 km weather grids.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
LABEL_DIR = DATA_DIR / "labels"
PROCESSED_DIR = DATA_DIR / "processed"
MODEL_DIR = ROOT / "models"
DB_PATH = Path(os.environ.get("KKH_DB_PATH", DATA_DIR / "app.db"))

for _d in (RAW_DIR, LABEL_DIR, PROCESSED_DIR, MODEL_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# History window used for training data
HISTORY_START = "2015-01-01"

# Risk bands for the probability that a segment is blocked tomorrow
RISK_BANDS = [
    (0.00, 0.20, "open", "Open", "کھلی"),
    (0.20, 0.50, "caution", "Delays possible", "تاخیر ممکن"),
    (0.50, 1.01, "blocked", "Likely blocked", "بند ہونے کا خدشہ"),
]


@dataclass(frozen=True)
class Segment:
    id: str
    name: str
    name_ur: str
    district: str
    lat: float           # weather sampling point
    lon: float
    elevation_m: int
    length_km: int
    susceptibility: float  # 0..1 prior landslide/flood susceptibility
    glacier_fed: bool      # exposed to glacial melt / GLOF floods
    places: tuple[str, ...]
    route: tuple[tuple[float, float], ...]
    seasonal_closure: tuple[str, str] | None = None  # ("MM-DD", "MM-DD") inclusive
    notes: str = ""


SEGMENTS: list[Segment] = [
    Segment(
        "HZR-BSM", "Havelian → Besham", "حویلیاں ← بشام", "Abbottabad / Mansehra / Battagram",
        34.62, 73.12, 1000, 190, 0.15, False,
        ("havelian", "abbottabad", "mansehra", "shinkiari", "battagram", "thakot",
         "chattar plain", "batgram"),
        ((34.05, 73.16), (34.15, 73.21), (34.33, 73.20), (34.47, 73.27),
         (34.68, 73.02), (34.86, 72.92), (34.93, 72.87)),
        notes="Wider valley road, mostly low risk; occasional slides near Thakot.",
    ),
    Segment(
        "BSM-DSU", "Besham → Dasu", "بشام ← داسو", "Shangla / Lower Kohistan",
        35.10, 73.08, 900, 75, 0.55, False,
        ("besham", "dubair", "pattan", "gloos banda", "kayal", "ranolia", "komila",
         "lower kohistan", "jalkot", "shangla"),
        ((34.93, 72.87), (35.00, 72.95), (35.08, 73.03), (35.18, 73.15), (35.29, 73.29)),
        notes="Steep Indus gorge; monsoon flash floods and rockfall.",
    ),
    Segment(
        "DSU-CHL", "Dasu → Chilas", "داسو ← چلاس", "Upper Kohistan / Diamer",
        35.42, 73.65, 1100, 125, 0.75, False,
        ("dasu", "lotar", "barsin", "barseen", "doga", "sazin", "shatial", "harban",
         "bhasha", "basha", "hodar", "thor", "upper kohistan", "kohistan"),
        ((35.29, 73.29), (35.40, 73.27), (35.52, 73.30), (35.53, 73.56),
         (35.52, 73.84), (35.42, 74.10)),
        notes="Most frequently blocked stretch; rain + snow triggered rockslides.",
    ),
    Segment(
        "CHL-JGL", "Chilas → Jaglot", "چلاس ← جگلوٹ", "Diamer",
        35.48, 74.40, 1200, 95, 0.70, False,
        ("chilas", "gini", "altashi", "bunar", "buner das", "bunar das", "tatta pani",
         "tattapani", "lal pari", "gandlu", "raikot", "talichi", "thalichi", "jaglot",
         "goner farm", "jalipur", "shing nala", "diamer"),
        ((35.42, 74.10), (35.41, 74.18), (35.45, 74.40), (35.49, 74.59),
         (35.57, 74.62), (35.69, 74.62)),
        notes="Tatta Pani and Lal Pari are chronic slide zones.",
    ),
    Segment(
        "JGL-GLT", "Jaglot → Gilgit", "جگلوٹ ← گلگت", "Gilgit",
        35.82, 74.52, 1450, 50, 0.30, False,
        ("partab pul", "pari", "danyor", "danyore", "gilgit", "sakwar", "jutial"),
        ((35.69, 74.62), (35.78, 74.60), (35.93, 74.38)),
    ),
    Segment(
        "GLT-HNZ", "Gilgit → Hunza (Aliabad)", "گلگت ← ہنزہ", "Gilgit / Nagar / Hunza",
        36.20, 74.42, 1900, 100, 0.40, True,
        ("jutal", "rahimabad", "nomal", "chalt", "sikanderabad", "nilt", "thol", "ghulmet",
         "pisan", "minapin", "hassanabad", "shishper", "aliabad", "karimabad", "ganish",
         "nagar", "hunza"),
        ((35.93, 74.38), (36.04, 74.38), (36.08, 74.27), (36.25, 74.31),
         (36.24, 74.46), (36.26, 74.55), (36.30, 74.60), (36.31, 74.68)),
        notes="Hassanabad bridge is exposed to Shishper glacier lake outbursts (GLOF).",
    ),
    Segment(
        "HNZ-SST", "Hunza → Sost (Gojal)", "ہنزہ ← سوست", "Hunza (Gojal)",
        36.48, 74.86, 2500, 85, 0.50, True,
        ("attabad", "ataabad", "shishkat", "gulmit", "juchar", "gojal", "passu", "hussaini",
         "khyber", "morkhun", "sost", "upper hunza"),
        ((36.31, 74.68), (36.34, 74.86), (36.39, 74.86), (36.47, 74.89),
         (36.57, 74.80), (36.69, 74.82)),
        notes="Glacial melt torrents (Juchar, Passu) in summer heat.",
    ),
    Segment(
        "SST-KJB", "Sost → Khunjerab Pass", "سوست ← خنجراب", "Hunza (Khunjerab NP)",
        36.80, 75.15, 3900, 85, 0.45, True,
        ("khunjerab", "dih", "koksil", "khunjerab pass", "pak-china border"),
        ((36.69, 74.82), (36.78, 74.95), (36.85, 75.05), (36.85, 75.43)),
        seasonal_closure=("12-01", "03-31"),
        notes="Pak-China border is closed to traffic every winter (typically Dec 1 – Mar 31).",
    ),
]

SEGMENT_IDS = [s.id for s in SEGMENTS]

# Road elevation profile, Havelian (km 0) -> Khunjerab Pass (km 805). Approximate, for the
# dashboard's long-section drawing. Segment boundaries follow cumulative segment lengths.
PROFILE = [
    (0, 850, "Havelian"), (25, 1250, ""), (55, 1090, "Mansehra"), (95, 1000, ""),
    (120, 1030, "Battagram"), (150, 700, "Thakot"), (190, 620, "Besham"), (225, 700, "Pattan"),
    (265, 800, "Dasu"), (300, 900, ""), (330, 1000, "Shatial"), (390, 1250, "Chilas"),
    (425, 1180, "Tatta Pani"), (450, 1150, "Raikot"), (485, 1300, "Jaglot"), (535, 1500, "Gilgit"),
    (565, 1750, "Chalt"), (600, 2000, "Hassanabad"), (635, 2150, "Aliabad"), (660, 2400, "Attabad"),
    (680, 2450, "Gulmit"), (695, 2500, "Passu"), (720, 2800, "Sost"), (760, 3400, ""),
    (790, 4200, ""), (805, 4693, "Khunjerab Pass"),
]
SEG_BY_ID = {s.id: s for s in SEGMENTS}

# Open-Meteo daily variables used everywhere (archive + forecast APIs share names)
WEATHER_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "snowfall_sum",
    "precipitation_hours",
    "wind_speed_10m_max",
]

# News sources (RSS) — no API keys needed
NEWS_FEEDS = [
    "https://news.google.com/rss/search?q=%22Karakoram+Highway%22&hl=en-PK&gl=PK&ceid=PK:en",
    "https://news.google.com/rss/search?q=KKH+blocked+OR+KKH+restored&hl=en-PK&gl=PK&ceid=PK:en",
    "https://news.google.com/rss/search?q=%D8%B4%D8%A7%DB%81%D8%B1%D8%A7%DB%81+%D9%82%D8%B1%D8%A7%D9%82%D8%B1%D9%85&hl=ur&gl=PK&ceid=PK:ur",
    "https://pamirtimes.net/feed/",
]

# NASA Global Landslide Catalog (COOLR export). Filtered to the KKH corridor at load.
NASA_GLC_URL = "https://data.nasa.gov/api/views/dd9e-wu2v/rows.csv?accessType=DOWNLOAD"

USER_AGENT = "KKH-Road-Predictor/1.0 (+https://github.com/)"


def in_seasonal_closure(segment: Segment, date) -> bool:
    """True if `date` falls inside the segment's fixed seasonal closure window."""
    if not segment.seasonal_closure:
        return False
    md = date.strftime("%m-%d")
    start, end = segment.seasonal_closure
    if start <= end:
        return start <= md <= end
    return md >= start or md <= end  # window wraps the new year


def risk_band(p: float) -> dict:
    for lo, hi, key, en, ur in RISK_BANDS:
        if lo <= p < hi:
            return {"key": key, "label": en, "label_ur": ur}
    return {"key": "blocked", "label": "Likely blocked", "label_ur": "بند ہونے کا خدشہ"}
