"""API contract + community reports."""
import pytest

from app.main import create_app
from kkh.config import SEGMENT_IDS


@pytest.fixture(scope="module")
def client():
    return create_app().test_client()


def test_health(client):
    j = client.get("/api/health").get_json()
    assert j["status"] == "ok" and len(j["replay_range"]) == 2


def test_replay_shape(client):
    j = client.get("/api/predict?date=2022-08-20").get_json()
    assert j["mode"] == "replay" and j["target_date"] == "2022-08-21"
    assert [s["id"] for s in j["segments"]] == SEGMENT_IDS
    for s in j["segments"]:
        assert 0 <= s["probability"] <= 1
        assert "actual" in s and "features" in s
    assert 0 <= j["summary"]["p_full_route_open"] <= 1


def test_winter_replay_uses_seasonal_rule(client):
    j = client.get("/api/predict?date=2023-01-10").get_json()
    kjb = next(s for s in j["segments"] if s["id"] == "SST-KJB")
    assert kjb["seasonal_closure"] and kjb["probability"] == 1.0 and kjb["band"]["key"] == "closed"


def test_bad_date_is_400(client):
    r = client.get("/api/predict?date=1990-01-01")
    assert r.status_code == 400 and "between" in r.get_json()["error"]


def test_live_always_answers(client):
    """Live works online; offline it must fall back to stored data and say so."""
    j = client.get("/api/predict").get_json()
    assert j["mode"] in ("live", "fallback")
    if j["mode"] == "fallback":
        assert "unavailable" in j["notice"]


def test_static_endpoints(client):
    assert len(client.get("/api/segments").get_json()["profile"]) > 10
    assert client.get("/api/model").get_json()["test"]["model"]["roc_auc"] > 0.5
    assert len(client.get("/api/model_export.json").get_json()["trees"]) > 10
    assert client.get("/api/route.geojson").get_json()["type"] == "FeatureCollection"
    assert isinstance(client.get("/api/scenarios").get_json(), list)
    assert client.get("/").status_code == 200


def test_report_validation_and_rate_limit(client):
    assert client.post("/api/reports", json={"segment_id": "NOPE", "status": "blocked"}).status_code == 400
    assert client.post("/api/reports", json={"segment_id": "DSU-CHL", "status": "flying"}).status_code == 400
    codes = [client.post("/api/reports", json={"segment_id": "DSU-CHL", "status": "blocked", "place": "Lotar"},
                         environ_base={"REMOTE_ADDR": "10.9.9.9"}).status_code for _ in range(6)]
    assert codes[:5] == [201] * 5 and codes[5] == 429
    listed = client.get("/api/reports").get_json()
    assert any(r["place"] == "Lotar" for r in listed) and all("client" not in r for r in listed)


def test_replay_features_match_training():
    """Audit regression test: the time machine must build exactly the training features."""
    import pandas as pd
    from kkh.config import PROCESSED_DIR
    from kkh.features import FEATURES, build_features
    from kkh.predict import get_predictor
    rd = lambda f: pd.read_csv(PROCESSED_DIR / f, parse_dates=["date"])
    full = build_features(rd("weather.csv"), rd("news_daily.csv"), rd("history.csv"))
    P = get_predictor()
    for d in ["2019-03-14", "2022-08-20", "2025-07-03"]:
        a = pd.DataFrame({s["id"]: s["features"] for s in P.replay(d)["segments"]}).T[FEATURES].astype(float)
        b = full[full.date == d].set_index("segment_id")[FEATURES].loc[a.index].astype(float)
        assert (a - b).abs().max().max() < 1e-4, (a - b).abs().max().sort_values().tail(3)


def test_trend(client):
    j = client.get("/api/trend?end=2022-08-20&days=7").get_json()
    assert len(j) == 7 and j[-1]["as_of"] == "2022-08-20"
    assert all(0 <= r["p_clear"] <= 1 for r in j)
