from copy import deepcopy
import json
from threading import Event
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.factor_dsl import FactorComputer, parse_expression
from backend.harness import HarnessRunner, new_run
from backend.harness_eval import evaluate_expression, portfolio, promotion_reason
from backend.harness_models import FactorProposal, HarnessRequest
from backend.harness_llm import AzureProposer
from backend.main import app
from backend.qlib_data import QlibData, QlibPanel
from backend.storage import ResearchStore


def panel():
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2020-01-01", periods=360)
    prices = np.exp(np.cumsum(rng.normal(.0005, .015, (360, 12)), axis=0)) * 20
    fields = {"open": prices.copy(), "close": prices.copy(), "high": prices * 1.01,
              "low": prices * .99, "volume": np.full(prices.shape, 1000.)}
    return QlibPanel(dates, [f"SH{600000 + i}" for i in range(12)], fields,
                     np.ones(prices.shape, dtype=bool), "test-fingerprint")


def config(p):
    return HarnessRequest(
        train_start=p.dates[30].date(), train_end=p.dates[139].date(),
        validation_start=p.dates[140].date(), validation_end=p.dates[249].date(),
        test_start=p.dates[250].date(), test_end=p.dates[-1].date(),
        min_samples=10, iterations=1, candidates_per_iteration=2,
    )


@pytest.mark.parametrize("expression", [
    "__import__('os')", "roc(-5)", "delay(roc(5),-1)", "delay(roc(5),0)", "roc(121)",
    "roc(5)[0]", "roc(5).__class__", "roc(5)**2", "[x for x in range(2)]",
    "mean(roc(5),2,3)", "roc(period=5)", "open('x')", "float('nan')", "True", "100",
])
def test_dsl_rejects_unsafe_or_future_operations(expression):
    with pytest.raises(ValueError):
        parse_expression(expression)


def test_dsl_prefix_invariance_and_membership_ranking():
    original = panel()
    expression = "mean(rank(roc(5))-0.3*rank(atr(14)),3)"
    first = FactorComputer(original).compute(expression)
    changed = deepcopy(original)
    for array in changed.fields.values():
        array[250:] *= 5
    changed.membership[250:] = False
    second = FactorComputer(changed).compute(expression)
    np.testing.assert_allclose(first[:250], second[:250], equal_nan=True)
    original.membership[:, 0] = False
    values = FactorComputer(original).compute("rank(roc(5))")
    assert np.isnan(values[:, 0]).all()
    assert np.nanmax(values) <= .5
    clipped = FactorComputer(original).compute("clip(roc(5),-0.1,0.1)")
    assert np.nanmin(clipped) >= -.1
    assert np.nanmax(clipped) <= .1


def test_qlib_native_reader_headers_and_historical_membership(tmp_path):
    p = panel()
    (tmp_path / "calendars").mkdir()
    (tmp_path / "instruments").mkdir()
    (tmp_path / "calendars" / "day.txt").write_text("\n".join(p.dates.strftime("%Y-%m-%d")), encoding="utf-8")
    records = []
    for i, symbol in enumerate(p.symbols):
        directory = tmp_path / "features" / symbol.lower()
        directory.mkdir(parents=True)
        records.append(f"{symbol}\t{p.dates[i].date()}\t{p.dates[300+i].date()}")
        for field in p.fields:
            np.r_[0, p.fields[field][:, i]].astype("<f4").tofile(directory / f"{field}.day.bin")
    (tmp_path / "instruments" / "csi300.txt").write_text("\n".join(records), encoding="utf-8")
    loaded = QlibData(tmp_path).load("csi300", p.dates[30], p.dates[-1])
    assert loaded.fields["close"][0, 0] == pytest.approx(p.fields["close"][0, 0])
    assert loaded.membership[0].sum() == 1
    assert not loaded.membership[-1].any()
    assert len(loaded.fingerprint) == 64
    path = tmp_path / "features" / p.symbols[0].lower() / "open.day.bin"
    np.array([-1, 1, 2], dtype="<f4").tofile(path)
    with pytest.raises(ValueError, match="索引"):
        QlibData(tmp_path).load("csi300", p.dates[30], p.dates[-1])


def test_portfolio_costs_and_missing_opens_do_not_replace_selection():
    p = panel()
    for field in ("open", "close", "high", "low"):
        p.fields[field][:] = 10
    cfg = config(p).model_copy(update={"top_fraction": .05, "horizon": 20})
    values = np.tile(np.arange(12), (360, 1)).astype(float)
    result = portfolio(p, values, cfg.test_start, cfg.test_end, cfg)
    expected = 1 / (1 + cfg.slippage_bps / 10000) / (1 + cfg.commission_bps / 10000)
    assert result["curve"][0]["nav"] == pytest.approx(expected)
    assert result["total_return"] == pytest.approx(expected - 1)
    p.fields["open"][250:, -1] = np.nan
    missing = portfolio(p, values, cfg.test_start, cfg.test_end, cfg)
    assert missing["total_return"] == 0
    assert missing["unfilled_orders"] > 0
    assert missing["traded_days"] == 0


def test_labels_are_purged_at_every_split_end():
    p = panel()
    cfg = config(p)
    values = FactorComputer(p).compute("roc(5)")
    first = evaluate_expression(p, values, cfg.validation_start, cfg.validation_end, cfg)
    assert all(row["ic"] is None for row in first["daily_ic"][-6:])
    changed = deepcopy(p)
    for field in changed.fields:
        changed.fields[field][250:] *= 10
    second = evaluate_expression(changed, values, cfg.validation_start, cfg.validation_end, cfg)
    assert first == second


def test_promotion_requires_both_metrics_and_risk_guards():
    cfg = config(panel())
    base = {"coverage": 1., "valid_days": 100, "ic_mean": .01, "icir": .1, "sharpe": 1., "max_drawdown": -.2}
    incumbent = {"train": base, "validation": base}
    better = {"train": base.copy(), "validation": {**base, "ic_mean": .02, "icir": .2}}
    assert promotion_reason(better, incumbent, base, cfg)[0]
    for key, value in (("ic_mean", .009), ("icir", .099), ("coverage", .5),
                       ("sharpe", .8), ("max_drawdown", -.26), ("icir", None)):
        candidate = deepcopy(better)
        candidate["validation"][key] = value
        assert not promotion_reason(candidate, incumbent, base, cfg)[0]


def test_runner_freezes_before_test_and_retains_invalid_experiments(tmp_path):
    p = panel()
    cfg = config(p)
    store = ResearchStore(tmp_path / "research.sqlite")
    document = new_run(cfg)
    store.save_harness(document)
    loads = []

    class Provider:
        def load(self, universe, start, end):
            loads.append(end)
            if end == cfg.test_end:
                saved = store.load_harness(document["id"])
                assert saved["stage"] == "freeze"
                assert saved["frozen_champion"]
            mask = p.dates <= pd.Timestamp(end)
            return QlibPanel(p.dates[mask], p.symbols, {k: a[mask] for k, a in p.fields.items()}, p.membership[mask], "test")

    class Proposer:
        def propose(self, iteration, count, feedback):
            return [
                FactorProposal(expression="delay(roc(5),-1)", hypothesis="invalid", change="invalid"),
                FactorProposal(expression="roc(5)", hypothesis="test short momentum", change="period"),
            ]

        def close(self):
            pass

    HarnessRunner(store, Provider()).execute(document["id"], Event(), Proposer())
    saved = store.load_harness(document["id"])
    assert saved["status"] == "completed", saved.get("error")
    assert loads == [cfg.validation_end, cfg.test_end]
    assert saved["experiments"][1]["status"] == "invalid"
    assert "holdout" in saved and saved["holdout"]["baseline"]["curve"]
    assert saved["holdout"]["champion"]["expression"] == saved["frozen_champion"]["expression"]
    assert ResearchStore(tmp_path / "research.sqlite").load_harness(document["id"]) == saved


def test_cancellation_and_invalid_dates(tmp_path):
    p = panel()
    cfg = config(p)
    with pytest.raises(ValidationError):
        HarnessRequest.model_validate({**cfg.model_dump(), "test_start": cfg.validation_start})
    store = ResearchStore(tmp_path / "research.sqlite")
    document = new_run(cfg)
    store.save_harness(document)
    cancelled = Event()
    cancelled.set()
    HarnessRunner(store).execute(document["id"], cancelled)
    saved = store.load_harness(document["id"])
    assert saved["status"] == "cancelled"
    assert saved["holdout"] is None


def test_sibling_proposals_keep_generation_parent_when_first_is_promoted(tmp_path, monkeypatch):
    p = panel()
    cfg = config(p)
    store = ResearchStore(tmp_path / "lineage.sqlite")
    document = new_run(cfg)
    store.save_harness(document)

    class Provider:
        def load(self, universe, start, end):
            mask = p.dates <= pd.Timestamp(end)
            return QlibPanel(p.dates[mask], p.symbols, {k: a[mask] for k, a in p.fields.items()}, p.membership[mask], "test")

    class Proposer:
        def propose(self, iteration, count, feedback):
            return [FactorProposal(expression=f"roc({n})", hypothesis="sibling", change="mutation", parent="incumbent")
                    for n in (5, 7)]

        def close(self):
            pass

    monkeypatch.setattr("backend.harness.promotion_reason", lambda *args: (True, "test promotion"))
    HarnessRunner(store, Provider()).execute(document["id"], Event(), Proposer())
    saved = store.load_harness(document["id"])
    assert saved["status"] == "completed"
    first, second = saved["experiments"][1:]
    assert first["parent_id"] == second["parent_id"] == "e0"
    assert second["compared_to"] == "e1"


def test_harness_api_status_and_not_found(monkeypatch, tmp_path):
    from backend import main

    monkeypatch.setattr(main, "ResearchStore", lambda: ResearchStore(tmp_path / "api.sqlite"))
    with TestClient(app) as client:
        response = client.get("/api/harness/meta")
        assert response.status_code == 200
        assert "api_key" not in response.json()["azure"]
        assert client.get("/api/harness/runs").json() == {"items": []}
        assert client.get("/api/harness/runs/00000000-0000-0000-0000-000000000000").status_code == 404
        assert client.post("/api/harness/runs", json={"test_start": "2021-01-01"}).status_code == 422


def test_azure_proposer_auth_protocol_and_bounded_public_feedback(monkeypatch):
    sent = []
    clients = []
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://unit-test.invalid")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "unit-test-placeholder")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "test-deployment")

    class Client:
        def __init__(self, **kwargs):
            clients.append(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            sent.append(kwargs)
            content = json.dumps({"proposals": [{"expression": "roc(5)", "hypothesis": "generic momentum", "change": "window"}]})
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

        def close(self):
            pass

    monkeypatch.setattr("openai.AzureOpenAI", Client)
    source = AzureProposer()
    assert source.propose(1, 1, [])[0].expression == "roc(5)"
    source.propose(2, 1, [{"outcome": "rejected"}])
    assert clients[0]["api_key"] == "unit-test-placeholder"
    assert sent[0]["model"] == "test-deployment"
    feedback = json.loads(sent[1]["messages"][1]["content"])
    assert feedback["generic_feedback"][0]["expression"] == "roc(5)"
    assert feedback["generic_feedback"][0]["outcome"] == "rejected"
    assert "unit-test-placeholder" not in json.dumps(sent)
    source.close()


def test_manager_recovers_interrupted_checkpoint_without_success_fallback(tmp_path):
    from backend.harness import HarnessManager

    store = ResearchStore(tmp_path / "recovery.sqlite")
    run = new_run(config(panel()))
    run["status"] = "running"
    store.save_harness(run)
    manager = HarnessManager(store)
    saved = store.load_harness(run["id"])
    assert saved["status"] == "interrupted"
    assert saved["holdout"] is None
    manager.close()


def test_active_process_lease_prevents_false_interruption_and_concurrent_start(tmp_path):
    from backend.harness import HarnessBusyError, HarnessLease, HarnessManager

    store = ResearchStore(tmp_path / "lease.sqlite")
    run = new_run(config(panel()))
    run["status"] = "running"
    store.save_harness(run)
    lease = HarnessLease(store.path)
    try:
        manager = HarnessManager(store)
        assert store.load_harness(run["id"])["status"] == "running"
        with pytest.raises(HarnessBusyError):
            manager.start(config(panel()))
        manager.close()
    finally:
        lease.close()
