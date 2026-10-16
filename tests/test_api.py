import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models import STRATEGIES


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as session:
        yield session


def test_real_metadata_and_search(client):
    meta = client.get("/api/meta").json()
    assert meta["days"] == 1643
    assert meta["securities"] == 5451
    assert meta["start"] == "2020-01-02"
    assert meta["end"] == "2026-10-16"
    assert len(meta["files"]) == 7
    assert meta["strategies"] == list(STRATEGIES)
    assert client.get("/api/symbols?q=600519").json()["items"] == ["600519.SH"]
    assert client.get("/api/health").json() == {"status": "ok"}


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_real_data_backtest(client, strategy):
    response = client.post("/api/backtest", json={
        "symbols": ["000001.SZ", "600519.SH", "300750.SZ"],
        "start": "2023-01-01", "end": "2026-10-16", "strategy": strategy,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["curve"]) == result["period"]["days"]
    assert len(result["indicators"]) == len(result["curve"])
    assert result["trades"]
    assert all(np.isfinite(point["nav"]) and point["nav"] > 0 for point in result["curve"])
    assert result["metrics"]["fees"] > 0
    assert result["metrics"]["final_equity"] == pytest.approx(result["curve"][-1]["equity"])
    assert result["period"]["end"] == "2026-10-16"
    assert result["config"]["strategy"] == strategy
    assert all(
        value is None or np.isfinite(value)
        for row in result["indicators"]
        for name, value in row.items() if name != "date"
    )


@pytest.mark.parametrize("payload", [
    {"symbols": ["NOT_REAL"]},
    {"start": "2019-01-01"},
    {"end": "2027-01-01"},
    {"start": "2024-01-06", "end": "2024-01-07"},
    {"fast": 50, "slow": 20},
    {"symbols": []},
    {"strategy": "ema", "fast": 50, "slow": 20},
    {"strategy": "donchian", "donchian_entry": 10, "donchian_exit": 20},
    {"strategy": "atr", "atr_multiplier": -1},
    {"strategy": "willr", "willr_buy": 80},
    {"strategy": "kdj", "stoch_k": 0},
])
def test_api_rejects_invalid_requests(client, payload):
    response = client.post("/api/backtest", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]


def test_real_data_all_strategy_dashboard(client):
    payload = {
        "symbols": ["000001.SZ", "600519.SH", "300750.SZ"],
        "start": "2023-01-01", "end": "2026-10-16",
    }
    response = client.post("/api/compare", json=payload)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["summary"]["total"] == 12
    assert report["summary"]["passed"] == 12
    assert report["summary"]["failed"] == 0
    assert len(report["benchmark"]) == report["period"]["days"]
    for row in report["results"]:
        single = client.post("/api/backtest", json={**payload, "strategy": row["strategy"]}).json()
        assert row["metrics"] == single["metrics"]
        assert row["config"]["symbols"] == payload["symbols"]
        assert all(check["status"] == "passed" for check in row["validation"]["checks"])
        assert len(row["curve"]) == len(report["benchmark"])


@pytest.mark.parametrize("payload", [
    {"symbols": ["NOT_REAL"]}, {"start": "2019-01-01"},
    {"start": "2024-01-06", "end": "2024-01-07"},
    {"strategy": "rsi", "fast": 50, "slow": 20},
    {"strategy": "macd", "donchian_entry": 5, "donchian_exit": 10},
])
def test_comparison_rejects_invalid_requests(client, payload):
    response = client.post("/api/compare", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]
