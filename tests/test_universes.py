from io import BytesIO
from urllib.error import URLError
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend import universes
from backend.main import app
from backend.models import BacktestRequest, ComparisonRequest
from backend.storage import ResearchStore
from backend.universes import UniverseError, parse_constituents, resolve_universe, sync_universe


def snapshot(identifier="hs300", version="a", symbols=None, stamp="2026-10-16T12:00:00+00:00"):
    return {"id": identifier, **universes.INDEXES[identifier], "symbols": symbols or ["000001.SZ", "600519.SH"],
            "as_of": "2026-10-16", "version": version * 64, "fetched_at": stamp,
            "source": "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/000300cons.xls",
            "warning": universes.SNAPSHOT_WARNING}


@pytest.fixture
def official_frame():
    return pd.DataFrame({
        "日期Date": ["20261016"] * 300, "指数代码 Index Code": ["000300"] * 300,
        "成份券代码Constituent Code": [f"{code:06d}" for code in range(300)],
        "交易所Exchange": ["深圳证券交易所"] * 299 + ["上海证券交易所"],
    })


def test_parser_uses_real_snapshot_date_and_exchange(official_frame, monkeypatch):
    def read_excel(stream, engine, dtype):
        assert stream.read() == b"xls"
        assert engine == "xlrd" and dtype is str
        return official_frame
    monkeypatch.setattr(universes.pd, "read_excel", read_excel)
    parsed = parse_constituents(b"xls", "hs300")
    assert parsed["as_of"] == "2026-10-16"
    assert len(parsed["symbols"]) == 300
    assert parsed["symbols"][0] == "000000.SZ"
    assert parsed["symbols"][-1] == "000299.SH"


@pytest.mark.parametrize("mutation", ["headers", "count", "index", "date", "mixed_dates", "code", "missing_code", "unicode", "exchange", "duplicate"])
def test_parser_rejects_invalid_official_files(official_frame, monkeypatch, mutation):
    frame = official_frame.copy()
    if mutation == "headers":
        frame = frame.drop(columns="日期Date")
    elif mutation == "count":
        frame = frame.iloc[:-1]
    elif mutation == "index":
        frame.loc[0, "指数代码 Index Code"] = "000905"
    elif mutation == "date":
        frame.loc[0, "日期Date"] = "not a date"
    elif mutation == "mixed_dates":
        frame.loc[0, "日期Date"] = "20261015"
    elif mutation == "code":
        frame.loc[0, "成份券代码Constituent Code"] = "1"
    elif mutation == "missing_code":
        frame.loc[0, "成份券代码Constituent Code"] = np.nan
    elif mutation == "unicode":
        frame.loc[0, "成份券代码Constituent Code"] = "０００００１"
    elif mutation == "exchange":
        frame.loc[0, "交易所Exchange"] = "unknown"
    else:
        frame.loc[0, "成份券代码Constituent Code"] = frame.loc[1, "成份券代码Constituent Code"]
    monkeypatch.setattr(universes.pd, "read_excel", lambda *args, **kwargs: frame)
    with pytest.raises(UniverseError):
        parse_constituents(b"xls", "hs300")


def test_parser_reports_non_workbook():
    with pytest.raises(UniverseError):
        parse_constituents(b"not excel", "hs300")


def test_sync_versions_and_source_failure_preserve_cache(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / "research.sqlite3")
    parsed = {"symbols": ["000001.SZ", "600519.SH"], "as_of": "2026-10-16"}
    calls = []
    def download(url, timeout):
        calls.append((url, timeout))
        return BytesIO(b"official workbook")
    monkeypatch.setattr(universes, "urlopen", download)
    monkeypatch.setattr(universes, "parse_constituents", lambda content, identifier: dict(parsed))
    first = sync_universe(store, "hs300")
    same = sync_universe(store, "hs300")
    assert first["version"] == same["version"]
    assert len(first["version"]) == 64
    assert calls[0] == (first["source"], 30)
    parsed["as_of"] = "2026-10-17"
    second = sync_universe(store, "hs300")
    assert second["version"] != first["version"]
    assert store.load_universe("hs300", first["version"])["as_of"] == "2026-10-16"
    assert store.load_universe("hs300") == second
    def fail(*args, **kwargs):
        raise URLError("offline")
    monkeypatch.setattr(universes, "urlopen", fail)
    with pytest.raises(UniverseError, match="缓存不会被覆盖"):
        sync_universe(store, "hs300")
    assert store.load_universe("hs300") == second


def test_oversized_source_is_rejected(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / "research.sqlite3")
    monkeypatch.setattr(universes, "urlopen", lambda *args, **kwargs: BytesIO(b"x" * 2_000_001))
    with pytest.raises(UniverseError, match="大小限制"):
        sync_universe(store, "hs300")
    assert store.load_universe("hs300") is None


def test_resolver_preserves_archived_version_and_comparison_type(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / "research.sqlite3")
    first = snapshot(symbols=["000001.SZ", "600519.SH", "MISSING"])
    second = snapshot(version="b", symbols=["300750.SZ"], stamp="2026-10-17T12:00:00+00:00")
    store.save_universe(first)
    store.save_universe(second)
    available = ["000001.SZ", "600519.SH", "300750.SZ"]
    config = ComparisonRequest(universe="hs300", universe_version=first["version"])
    resolved, info = resolve_universe(store, config, available)
    assert isinstance(resolved, ComparisonRequest)
    assert resolved.symbols == available[:2]
    assert info["matched"] == 2 and info["missing"] == ["MISSING"]
    assert resolved.universe_version == first["version"]
    assert resolve_universe(store, BacktestRequest(), available)[0].symbols == ["300750.SZ"]
    monkeypatch.setattr(universes, "sync_universe", lambda *args: pytest.fail("An archived version cannot fetch latest"))
    with pytest.raises(ValueError, match="版本不存在"):
        resolve_universe(store, BacktestRequest(universe_version="c" * 64), available)
    with pytest.raises(ValueError, match="没有匹配"):
        resolve_universe(store, BacktestRequest(), ["OTHER"])


def test_manual_compatibility_and_default_index(tmp_path):
    store = ResearchStore(tmp_path / "research.sqlite3")
    assert BacktestRequest().universe == "hs300"
    manual = BacktestRequest(symbols=["000001.SZ"])
    assert manual.universe == "custom"
    assert resolve_universe(store, manual, manual.symbols)[0] is manual
    with pytest.raises(ValueError, match="未知证券"):
        resolve_universe(store, manual, ["OTHER"])


@pytest.fixture
def client(tmp_path):
    with TestClient(app) as session:
        app.state.store = ResearchStore(tmp_path / "api.sqlite3")
        yield session


def rule_document(name="API custom"):
    def rule(operator):
        return {"left": {"kind": "rsi"}, "operator": operator, "right": {"type": "constant", "value": 50}}
    return {"name": name, "entry": {"mode": "all", "rules": [rule("gt")]},
            "exit": {"mode": "any", "rules": [rule("lte")]}}


def test_strategy_crud_api_and_missing_records(client):
    assert client.get("/api/strategies").json() == {"items": []}
    created = client.post("/api/strategies", json=rule_document())
    assert created.status_code == 201
    saved = created.json()
    assert client.get("/api/strategies").json()["items"] == [saved]
    updated = client.put(f"/api/strategies/{saved['id']}", json=rule_document("Renamed"))
    assert updated.status_code == 200
    assert updated.json()["definition"]["name"] == "Renamed"
    assert client.put(f"/api/strategies/{uuid4()}", json=rule_document()).status_code == 404
    assert client.delete(f"/api/strategies/{saved['id']}").json() == {"deleted": True}
    assert client.delete(f"/api/strategies/{saved['id']}").status_code == 404
    assert client.get("/api/strategies").json() == {"items": []}
    assert client.post("/api/strategies", json={**rule_document(), "name": ""}).status_code == 422


def test_default_api_executes_all_300_members_and_freezes_version(client):
    members = app.state.market.symbols[:300]
    app.state.store.save_universe(snapshot(symbols=members))
    response = client.post("/api/backtest", json={})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["config"]["universe"] == "hs300"
    assert result["config"]["symbols"] == members
    assert result["config"]["universe_version"] == "a" * 64
    assert result["universe"]["matched"] == 300
    assert any("前视偏差" in warning for warning in result["warnings"])
    assert any("不是该指数的官方收益" in warning for warning in result["warnings"])
    items = client.get("/api/universes").json()["items"]
    assert len(items) == 4 and items[0]["matched"] == 300
    assert client.post("/api/backtest", json={"symbols": members[:21]}).status_code == 422
    assert client.post("/api/backtest", json={"universe_version": "b" * 64}).status_code == 422


def test_source_failure_is_explicit_and_never_uses_fake_members(client, monkeypatch):
    def fail(*args, **kwargs):
        raise UniverseError("official source offline")
    monkeypatch.setattr(universes, "sync_universe", fail)
    response = client.post("/api/backtest", json={})
    assert response.status_code == 503
    assert response.json()["detail"] == "official source offline"
    assert app.state.store.load_universe("hs300") is None
    assert client.post("/api/universes/custom/sync").status_code == 422


def test_custom_api_and_batch_use_embedded_definition_not_mutable_saved_record(client):
    document = rule_document()
    saved = client.post("/api/strategies", json=document).json()
    payload = {"symbols": ["000001.SZ", "600519.SH"], "start": "2024-01-01", "end": "2024-12-31",
               "strategy": "custom", "custom_strategy": document}
    response = client.post("/api/backtest", json=payload)
    assert response.status_code == 200, response.text
    first = response.json()
    client.put(f"/api/strategies/{saved['id']}", json=rule_document("Changed after report"))
    assert client.post("/api/backtest", json=first["config"]).json()["metrics"] == first["metrics"]
    report = client.post("/api/compare", json=payload).json()
    assert report["summary"]["total"] == 13
    assert report["summary"]["failed"] == 0
    assert report["results"][-1]["metrics"] == first["metrics"]
    assert report["results"][-1]["config"]["custom_strategy"] == first["config"]["custom_strategy"]
