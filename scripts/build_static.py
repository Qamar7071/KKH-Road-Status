"""
Build a single self-contained HTML demo (no server needed) from the same frontend.

    python scripts/build_static.py            # -> dist/kkh-road-status.html

It embeds: segment metadata, the model card, the trained trees (for the what-if tool),
storm-day scenarios and pre-computed replays for the last 21 stored evenings + scenarios.
Good for GitHub Pages or any static host. Live forecasts need the Flask app.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from kkh.model import EXPORT_PATH  # noqa: E402
from kkh.predict import get_predictor, model_card, profile, segment_info  # noqa: E402
from kkh.config import RISK_BANDS  # noqa: E402

APP = ROOT / "app"
DIST = ROOT / "dist"


def build(out: Path = DIST / "kkh-road-status.html", recent_days: int = 21, full_document: bool = False) -> Path:
    p = get_predictor()
    lo, hi = p.data_range
    scenarios = p.notable_dates()
    dates = {s["as_of"] for s in scenarios}
    dates |= {(hi - pd.Timedelta(days=i)).date().isoformat() for i in range(recent_days)}
    replays = {d: p.replay(d) for d in sorted(dates)}
    bundle = {
        "segments": {"segments": segment_info(), "profile": profile(),
                     "bands": [{"from": a, "to": min(b, 1.0), "key": k, "label": en, "label_ur": ur}
                               for a, b, k, en, ur in RISK_BANDS]},
        "model": model_card(),
        "geo": json.loads((APP / "static" / "geo.json").read_text()),
        "export": json.loads(EXPORT_PATH.read_text()),
        "scenarios": scenarios,
        "trends": {d: p.trend(d, 14) for d in sorted(dates)},
        "replays": replays,
        "latest": hi.date().isoformat(),
        "range": [lo.date().isoformat(), hi.date().isoformat()],
    }
    page = (APP / "templates" / "_page.html").read_text(encoding="utf-8")
    css = (APP / "static" / "style.css").read_text(encoding="utf-8")
    js = (APP / "static" / "fx.js").read_text(encoding="utf-8") + "\n" + (APP / "static" / "app.js").read_text(encoding="utf-8")
    data = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    logo = "data:image/webp;base64," + base64.b64encode((APP / "static" / "logo.webp").read_bytes()).decode()
    page = page.replace('src="static/logo.webp"', f'src="{logo}"')
    uri = lambda f: "data:image/webp;base64," + base64.b64encode((APP / "static" / f).read_bytes()).decode()
    page = page.replace('src="static/hero.webp"', f'src="{uri("hero.webp")}"')
    css = css.replace('url("hero-blur.webp")', f'url("{uri("hero-blur.webp")}")')
    page = page.replace("<!--STYLE-->", f"<style>\n{css}</style>")
    page = page.replace("<!--SCRIPT-->", f"<script>window.__KKH_BUNDLE__={data};</script>\n<script>\n{js}</script>")
    if full_document:
        page = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
                '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
                'body{margin:0}[hidden]{display:none!important}</style></head><body>\n' + page + "\n</body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


if __name__ == "__main__":
    a = build()                                                   # artifact body (host adds <head>)
    b = build(DIST / "index.html", full_document=True)            # full page for GitHub Pages
    for f in (a, b):
        print(f"{f.relative_to(ROOT)}  {f.stat().st_size // 1024} KB")
