import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.evaluation import correlation_summary, cross_section_correlation, quantile_analysis, run_factor_evaluation
from backend.main import app
from backend.models import BacktestRequest, FactorEvaluationRequest, IndicatorSpec


def market(days=50, securities=6):
    dates = pd.bdate_range("2024-01-01", periods=days)
    values = 10 + np.arange(days)[:, None] * np.linspace(0.01, 0.3, securities)[None, :]
    close = pd.DataFrame(values, index=dates, columns=[f"S{i}" for i in range(securities)])
    return {
        "open": close.copy(), "close": close, "high": close + 1, "low": close - 1,
        "cumadj": close * 0 + 1, "amt": close * 0 + 1000,
    }


def settings(data, **updates):
    return FactorEvaluationRequest(
        config=BacktestRequest(symbols=list(data["close"].columns),
                               start=data["close"].index[0].date(), end=data["close"].index[-1].date()),
        factors=[IndicatorSpec(kind="roc", period=2)], horizons=[1, 5, 20],
        primary_horizon=5, groups=3, min_samples=3, **updates,
    )


def test_correlations_ties_constants_and_pairwise_missing():
    a = np.array([[1, 2, 2, 4], [3, 3, 3, 3], [1, np.nan, 2, 3]], dtype=float)
    b = np.array([[4, 3, 3, 1], [1, 2, 3, 4], [1, 999, 4, 9]], dtype=float)
    ranked = cross_section_correlation(a, b, 3, rank=True)
    assert ranked[0] == pytest.approx(-1)
    assert np.isnan(ranked[1])
    assert ranked[2] == pytest.approx(1)
    pearson = cross_section_correlation(a, b, 3)
    assert pearson[2] == pytest.approx(np.corrcoef([1, 2, 3], [1, 4, 9])[0, 1])
    assert np.isnan(cross_section_correlation(a, b, 4)[2])


def test_icir_uses_sample_std_and_positive_rate_excludes_missing():
    ic = np.array([-.2, .1, .4, np.nan])
    rank_ic = np.array([np.nan, -.5, .5, np.nan])
    summary = correlation_summary(ic, rank_ic)
    assert summary["ic_mean"] == pytest.approx(.1)
    assert summary["ic_std"] == pytest.approx(.3)
    assert summary["icir"] == pytest.approx(1 / 3)
    assert summary["ic_positive_rate"] == pytest.approx(2 / 3)
    assert summary["valid_days"] == 3
    assert summary["rank_ic_std"] == pytest.approx(np.sqrt(.5))
    assert summary["rank_ic_positive_rate"] == .5
    assert summary["rank_valid_days"] == 2
    empty = correlation_summary(np.array([np.nan]), np.array([1.]))
    assert empty["ic_mean"] is None
    assert empty["valid_days"] == 0
    assert empty["rank_icir"] is None


def test_real_indicator_ic_coverage_and_no_false_icir():
    data = market()
    report = run_factor_evaluation(data, settings(data))
    row = report["results"][0]
    assert row["metrics"]["rank_ic_mean"] == pytest.approx(1)
    assert row["metrics"]["rank_icir"] is None
    assert row["coverage"] == pytest.approx(48 / 50)
    assert row["metrics"]["rank_valid_days"] == 42
    assert report["correlations"][0]["value"] == pytest.approx(1)
    assert row["quantiles"]["periods"] == 8
    assert row["quantiles"]["skipped_periods"] == 1
    assert row["quantiles"]["monotonicity"] == pytest.approx(1)
    json.dumps(report, allow_nan=False)


def test_labels_use_next_open_and_do_not_reach_past_end():
    data = market()
    definition = settings(data)
    report = run_factor_evaluation(data, definition)
    first = report["results"][0]["quantiles"]["curve"][0]
    assert first["signal_date"] == data["close"].index[5].strftime("%Y-%m-%d")
    assert first["entry_date"] == data["close"].index[6].strftime("%Y-%m-%d")
    assert first["date"] == data["close"].index[11].strftime("%Y-%m-%d")
    expected = data["open"].iloc[11].to_numpy() / data["open"].iloc[6].to_numpy() - 1
    np.testing.assert_allclose(first["returns"], [expected[:2].mean(), expected[2:4].mean(), expected[4:].mean()])
    last = report["results"][0]["daily"][-6:]
    assert all(row["ic"] is None and row["samples"] == 0 for row in last)
    longer = market(days=70)
    replay = run_factor_evaluation(longer, definition)
    assert replay["results"] == report["results"]
    assert replay["correlations"] == report["correlations"]


def test_group_nav_and_spread_are_nonoverlapping_and_ties_not_split():
    dates = pd.bdate_range("2024-01-01", periods=9)
    factor = np.tile(np.arange(6), (9, 1)).astype(float)
    returns = np.tile([-.1, -.1, 0, 0, .1, .1], (9, 1))
    result = quantile_analysis(factor, returns, dates, 2, 3, 3)
    assert result["periods"] == 3
    assert result["curve"][-1]["nav"] == pytest.approx([.9 ** 3, 1, 1.1 ** 3])
    assert result["curve"][-1]["cumulative_spread"] == pytest.approx(.6)
    constant = quantile_analysis(factor * 0, returns, dates, 2, 3, 3)
    assert constant["periods"] == 0
    assert constant["groups"][0]["total_return"] is None
    assert constant["monotonicity"] is None


def test_adjustments_normalization_liquidity_and_warmup():
    data = market()
    definition = settings(data)
    split = {name: frame.copy() for name, frame in data.items()}
    for field in ("close", "open", "high", "low"):
        split[field].iloc[25:] /= 2
    split["cumadj"].iloc[25:] = 2
    original = run_factor_evaluation(data, definition)
    adjusted = run_factor_evaluation(split, definition)
    assert original["results"] == adjusted["results"]
    shorter = definition.model_copy(update={"config": definition.config.model_copy(update={
        "start": data["close"].index[10].date(), "min_amount": 500,
    })})
    data["amt"].iloc[12, :4] = 0
    result = run_factor_evaluation(data, shorter)["results"][0]
    assert result["daily"][0]["coverage"] == 1
    assert result["daily"][2]["coverage"] == pytest.approx(2 / 6)
    assert result["daily"][2]["ic"] is None
    prices = definition.model_copy(update={"factors": [IndicatorSpec(kind="sma", period=2)]})
    normalized = run_factor_evaluation(data, prices)["results"][0]
    assert normalized["transform"] == "指标 / 复权收盘 - 1"
    assert normalized["metrics"]["rank_ic_mean"] == pytest.approx(-1)
    raw = run_factor_evaluation(data, prices.model_copy(update={"normalization": "raw"}))["results"][0]
    assert raw["metrics"]["rank_ic_mean"] == pytest.approx(1)
    assert any("量纲" in warning for warning in raw["warnings"])


@pytest.mark.parametrize("changes", [
    {"horizons": [0, 5]}, {"horizons": [5, 61]}, {"horizons": [5, 5]}, {"horizons": [1]},
    {"groups": 4, "min_samples": 3}, {"factors": []},
    {"factors": [{"kind": "rsi"}, {"kind": "rsi"}]}, {"min_samples": 2},
])
def test_reject_invalid_evaluation(changes):
    payload = settings(market()).model_dump()
    with pytest.raises(ValidationError):
        FactorEvaluationRequest.model_validate({**payload, **changes})


def test_insufficient_market_samples_and_interval():
    data = market()
    with pytest.raises(ValueError, match="股票池"):
        run_factor_evaluation(data, settings(data).model_copy(update={"min_samples": 10}))
    short = settings(data).model_copy(update={"config": settings(data).config.model_copy(update={
        "start": data["close"].index[-5].date(),
    })})
    with pytest.raises(ValueError, match="区间不足"):
        run_factor_evaluation(data, short)


def test_evaluation_endpoint_real_data_and_rejections():
    with TestClient(app) as client:
        payload = {
            "config": {"symbols": ["000001.SZ", "600519.SH", "300750.SZ"],
                       "start": "2023-01-01", "end": "2026-10-16"},
            "factors": [{"kind": "rsi"}, {"kind": "roc", "period": 12}],
            "groups": 3, "min_samples": 3,
        }
        response = client.post("/api/factors/evaluate", json=payload)
        assert response.status_code == 200, response.text
        report = response.json()
        assert len(report["results"]) == 2
        assert len(report["correlations"]) == 3
        assert report["universe"]["matched"] == 3
        assert report["request"]["config"]["symbols"] == payload["config"]["symbols"]
        assert all(row["metrics"]["valid_days"] > 20 for row in report["results"])
        assert all(row["quantiles"]["periods"] > 0 for row in report["results"])
        for changes in ({"min_samples": 10}, {"config": {"symbols": ["INVALID"]}},
                        {"config": {**payload["config"], "start": "2019-01-01"}}):
            error = client.post("/api/factors/evaluate", json={**payload, **changes})
            assert error.status_code == 422
            assert error.json()["detail"]
