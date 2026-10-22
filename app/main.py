"""
Flask app: JSON API + dashboard.

    flask --app app.main run            # dev
    gunicorn "app.main:create_app()"    # production (see Dockerfile / render.yaml)

Endpoints
  GET  /api/health                 liveness + model/data info
  GET  /api/predict                live forecast for tomorrow (cached 30 min)
  GET  /api/predict?date=YYYY-MM-DD  time-machine replay of a past evening
  GET  /api/scenarios              notable past storm days for the replay
  GET  /api/trend?end=&days=14      clear-run chance for recent evenings (sparkline)
  GET  /api/segments               segment metadata
  GET  /api/route.geojson          KKH polyline per segment (for the map)
  GET  /api/model                  model card: metrics, calibration, drivers
  GET  /api/model_export.json      trained trees as JSON (runs the what-if tool in the browser)
  GET  /api/reports                community reports, last 48 h
  POST /api/reports                {segment_id, status, place?, note?}
"""
from __future__ import annotations

import hashlib
import os

from flask import Flask, jsonify, render_template, request, send_file

from kkh import store
from kkh.config import RISK_BANDS, SEGMENT_IDS
from kkh.model import EXPORT_PATH
from kkh.predict import get_predictor, model_card, profile, route_geojson, segment_info

MAX_REPORTS_PER_HOUR = 5


def _client_id() -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    return hashlib.sha256((ip + os.environ.get("SECRET_KEY", "kkh")).encode()).hexdigest()[:16]


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["JSON_SORT_KEYS"] = False
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    @app.after_request
    def _headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if request.path.startswith("/api/"):
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Cache-Control"] = "no-store" if request.method == "POST" else "public, max-age=300"
        return resp

    @app.errorhandler(ValueError)
    def _bad(e):
        return jsonify(error=str(e)), 400

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        p = get_predictor()
        lo, hi = p.data_range
        return jsonify(status="ok", data_mode=p.meta.get("mode"), trained_at=p.report.get("trained_at"),
                       replay_range=[str(lo.date()), str(hi.date())])

    @app.get("/api/predict")
    def predict():
        p = get_predictor()
        date = request.args.get("date")
        if date:
            return jsonify(p.replay(date))
        return jsonify(p.live(force=request.args.get("refresh") == "1"))

    @app.get("/api/trend")
    def trend():
        days = max(2, min(31, int(request.args.get("days", 14))))
        return jsonify(get_predictor().trend(request.args.get("end"), days))

    @app.get("/api/scenarios")
    def scenarios():
        return jsonify(get_predictor().notable_dates())

    @app.get("/api/segments")
    def segments():
        return jsonify(segments=segment_info(), profile=profile(),
                       bands=[{"from": lo, "to": min(hi, 1.0), "key": k, "label": en, "label_ur": ur}
                              for lo, hi, k, en, ur in RISK_BANDS])

    @app.get("/api/route.geojson")
    def route():
        return jsonify(route_geojson())

    @app.get("/api/model")
    def model():
        return jsonify(model_card())

    @app.get("/api/model_export.json")
    def model_export():
        return send_file(EXPORT_PATH, mimetype="application/json", max_age=3600)

    @app.get("/api/reports")
    def list_reports():
        return jsonify(store.recent_reports())

    @app.post("/api/reports")
    def add_report():
        body = request.get_json(silent=True) or request.form.to_dict()
        if body.get("segment_id") not in SEGMENT_IDS:
            raise ValueError("segment_id must be one of " + ", ".join(SEGMENT_IDS))
        cid = _client_id()
        if store.count_reports_from(cid) >= MAX_REPORTS_PER_HOUR:
            return jsonify(error="Too many reports — please try again later."), 429
        rec = store.add_report(body["segment_id"], body.get("status", ""), body.get("place", ""),
                               body.get("note", ""), client=cid)
        get_predictor()._live_cache = None      # next live call includes the new report
        return jsonify(rec), 201

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
