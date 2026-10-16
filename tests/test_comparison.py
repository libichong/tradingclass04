from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from backend.comparison import run_comparison, validate_backtest
from backend.engine import run_backtest
from backend.models import STRATEGIES, BacktestRequest, ComparisonRequest


@pytest.fixture(scope="module")
def sample():
    dates = pd.bdate_range("2024-01-01", periods=160)
    close = pd.DataFrame({"TEST": 30 + np.sin(np.arange(160) / 5) * 5}, index=dates)
    data = {
        "open": close.copy(), "high": close + 0.8, "low": close - 0.8,
        "close": close, "cumadj": close * 0 + 1,
        "amt": close * 0 + 1e8, "num_trades": close * 0 + 100,
    }
    config = ComparisonRequest(symbols=["TEST"], start=dates[0].date(), end=dates[-1].date())
    return data, config


def test_batch_reuses_single_strategy_engine_and_shared_settings(sample):
    data, config = sample
    report = run_comparison(data, config)
    assert report["summary"]["total"] == len(STRATEGIES)
    assert report["summary"]["failed"] == 0
    assert report["summary"]["passed"] + report["summary"]["warning"] == len(STRATEGIES)
    assert [row["strategy"] for row in report["results"]] == list(STRATEGIES)
    for row in report["results"]:
        single_config = BacktestRequest.model_validate({**config.model_dump(), "strategy": row["strategy"]})
        single = run_backtest(data, single_config)
        assert row["metrics"] == single["metrics"]
        assert row["monthly"] == single["monthly"]
        assert row["curve"] == [
            {key: point[key] for key in ("date", "nav", "drawdown")} for point in single["curve"]
        ]
        assert len(row["validation"]["checks"]) == 8
        prefix = next(check for check in row["validation"]["checks"] if check["id"] == "prefix")
        assert prefix["status"] == "passed"


@pytest.mark.parametrize("corruption,expected_failure", [
    ("nav", "metrics"), ("fee", "metrics"), ("notional", "ledger"),
    ("monthly", "monthly"), ("benchmark", "benchmark"),
    ("indicator", "prefix"), ("asset", "ledger"),
])
def test_validation_detects_inconsistent_results(sample, corruption, expected_failure):
    data, shared = sample
    config = BacktestRequest.model_validate(shared.model_dump())
    result = deepcopy(run_backtest(data, config))
    benchmark = [{"date": point["date"], "nav": point["benchmark"]} for point in result["curve"]]
    if corruption == "nav":
        result["curve"][0]["nav"] *= 1.1
    elif corruption == "fee":
        result["metrics"]["fees"] += 10
    elif corruption == "notional":
        result["trades"][0]["notional"] *= 1.1
    elif corruption == "monthly":
        result["monthly"][0]["return"] += 0.1
    elif corruption == "benchmark":
        benchmark[0]["nav"] += 0.1
    elif corruption == "indicator":
        result["indicators"][0]["close"] *= 1.1
    else:
        result["metrics"]["final_equity"] += 10
    validation = validate_backtest(result, data, config, benchmark)
    assert validation["status"] == "failed"
    assert next(item for item in validation["checks"] if item["id"] == expected_failure)["status"] == "failed"


def test_short_ranges_explicitly_skip_prefix_verification(sample):
    data, config = sample
    short = {name: frame.iloc[:2] for name, frame in data.items()}
    settings = ComparisonRequest.model_validate({**config.model_dump(), "end": short["close"].index[-1].date()})
    report = run_comparison(short, settings)
    assert report["summary"]["failed"] == 0
    assert report["summary"]["warning"] == len(STRATEGIES)
    for row in report["results"]:
        assert next(item for item in row["validation"]["checks"] if item["id"] == "prefix")["status"] == "skipped"


def test_no_trades_is_not_treated_as_strategy_success(sample):
    data, config = sample
    flat = {name: frame * 0 + 30 for name, frame in data.items()}
    report = run_comparison(flat, config)
    macd = next(row for row in report["results"] if row["strategy"] == "macd")
    assert macd["metrics"]["trade_count"] == 0
    assert macd["metrics"]["sharpe"] is None
    assert macd["metrics"]["win_rate"] is None
    assert macd["validation"]["status"] == "warning"


@pytest.mark.parametrize("values", [
    {"strategy": "rsi", "fast": 40, "slow": 20},
    {"strategy": "macd", "donchian_entry": 5, "donchian_exit": 10},
])
def test_batch_validates_parameters_for_all_strategies(values):
    with pytest.raises(ValidationError):
        ComparisonRequest(**values)
