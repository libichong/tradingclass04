from typing import get_args

import numpy as np
import pandas as pd
import pytest
import talib
from pydantic import ValidationError

from backend.comparison import run_comparison
from backend.engine import run_backtest
from backend.indicators import calculate_indicator, custom_catalog, custom_factors, custom_groups, evaluate_group
from backend.models import BacktestRequest, ComparisonRequest, CustomStrategy, IndicatorKind, IndicatorSpec, RuleGroup
from backend.storage import ResearchStore


def condition(kind="close", operator="gt", value=30):
    return {"left": {"kind": kind}, "operator": operator, "right": {"type": "constant", "value": value}}


@pytest.fixture
def definition():
    return CustomStrategy(name="Test rules", entry={"mode": "all", "rules": [condition()]},
                          exit={"mode": "any", "rules": [condition(operator="lt")]})


@pytest.fixture
def sample():
    dates = pd.bdate_range("2024-01-01", periods=160)
    close = pd.DataFrame({"TEST": 30 + np.sin(np.arange(160) / 5) * 5}, index=dates)
    return {"open": close.copy(), "close": close, "high": close + 1, "low": close - 1,
            "cumadj": close * 0 + 1, "amt": close * 0 + 1e8, "num_trades": close * 0 + 100}


@pytest.mark.parametrize("kind", get_args(IndicatorKind))
def test_custom_indicator_matches_talib_and_has_no_future_dependency(kind):
    close = 30 + np.sin(np.arange(160) / 5) * 5
    high, low = close + 1, close - 1
    spec = IndicatorSpec(kind=kind, period=7, fast=4, slow=9, signal=3, k=2, d=3, width=1.5)
    dif, dea, hist = talib.MACD(close, 4, 9, 3)
    k, d = talib.STOCH(high, low, close, 7, 2, 0, 3, 0)
    upper, middle, lower = talib.BBANDS(close, 7, 1.5, 1.5, 0)
    expected = {
        "close": close, "sma": talib.SMA(close, 7), "ema": talib.EMA(close, 7),
        "rsi": talib.RSI(close, 7), "roc": talib.ROC(close, 7),
        "macd_dif": dif, "macd_dea": dea, "macd_hist": hist * 2,
        "k": k, "d": d, "j": 3 * k - 2 * d,
        "cci": talib.CCI(high, low, close, 7), "adx": talib.ADX(high, low, close, 7),
        "plus_di": talib.PLUS_DI(high, low, close, 7), "minus_di": talib.MINUS_DI(high, low, close, 7),
        "willr": talib.WILLR(high, low, close, 7), "atr": talib.ATR(high, low, close, 7),
        "boll_upper": upper, "boll_middle": middle, "boll_lower": lower,
        "donchian_upper": np.r_[np.nan, talib.MAX(high, 7)[:-1]],
        "donchian_lower": np.r_[np.nan, talib.MIN(low, 7)[:-1]],
    }[kind]
    actual = calculate_indicator(spec, close, high, low)
    np.testing.assert_allclose(actual, expected, equal_nan=True)
    np.testing.assert_allclose(calculate_indicator(spec, close[:80], high[:80], low[:80]), actual[:80], equal_nan=True)
    assert np.isfinite(actual[-1])


@pytest.mark.parametrize("operator,expected", [
    ("gt", [False, False, False, False, False, False, True, False, True]),
    ("gte", [False, False, True, True, False, False, True, False, True]),
    ("lt", [False, True, False, False, True, False, False, True, False]),
    ("lte", [False, True, True, True, True, False, False, True, False]),
    ("cross_above", [False, False, False, False, False, False, False, False, True]),
    ("cross_below", [False, False, False, False, True, False, False, True, False]),
])
def test_rule_operators_and_missing_previous_day(operator, expected):
    values = np.array([np.nan, 1, 2, 2, 1, np.nan, 3, 1, 3])
    group = RuleGroup(rules=[condition(operator=operator, value=2)])
    np.testing.assert_array_equal(evaluate_group(group, {"CLOSE": values}), expected)


def test_and_or_and_indicator_operand():
    rules = [condition(operator="gt", value=2), condition(operator="lt", value=2)]
    lines = {"CLOSE": np.array([1., 2., 3., np.nan])}
    assert evaluate_group(RuleGroup(mode="all", rules=rules), lines).tolist() == [False] * 4
    assert evaluate_group(RuleGroup(mode="any", rules=rules), lines).tolist() == [True, False, True, False]
    rule = condition(operator="cross_above")
    rule["right"] = {"type": "indicator", "indicator": {"kind": "sma", "period": 2}}
    lines["SMA(2)"] = np.array([2., 1., 3., 2.])
    assert evaluate_group(RuleGroup(rules=[rule]), lines).tolist() == [False, True, False, False]


def test_exit_priority_and_missing_day_holds_state():
    definition = CustomStrategy(name="Priority", entry={"rules": [condition(value=0)]},
                                exit={"rules": [condition(value=2)]})
    close = np.array([1., 2., 3., np.nan, 2., 3.])
    targets, _ = custom_factors(close, close + 1, close - 0.1, definition)
    assert targets.tolist() == [True, True, False, False, True, False]
    definition = CustomStrategy(name="Hold", entry={"rules": [condition(value=2)]},
                                exit={"rules": [condition(operator="lt", value=1)]})
    close = np.array([3., np.nan, 2., 0.5])
    assert custom_factors(close, close + 1, close - 0.1, definition)[0].tolist() == [True, True, True, False]


def test_hlc_missing_bars_are_not_filled():
    close = np.arange(1., 31.)
    high, low = close + 1, close - 0.5
    high[15] = np.nan
    low[18] = close[18] + 1
    spec = IndicatorSpec(kind="atr", period=3)
    actual = calculate_indicator(spec, close, high, low)
    valid = np.ones(30, dtype=bool)
    valid[[15, 18]] = False
    np.testing.assert_allclose(actual[valid], talib.ATR(high[valid], low[valid], close[valid], 3), equal_nan=True)
    assert np.isnan(actual[[15, 18]]).all()


def test_catalog_deduplicates_and_atr_has_own_axis(definition):
    definition.entry.rules.append(definition.entry.rules[0].model_copy(deep=True))
    definition.exit.rules.append(type(definition.exit.rules[0]).model_validate(condition(kind="atr")))
    catalog = custom_catalog(definition)
    assert len(catalog) == 2
    assert [key for key, _ in catalog] == ["indicator_1", "indicator_2"]
    groups = {group["name"]: group["lines"] for group in custom_groups(definition)}
    assert groups["ATR"] == [{"key": "indicator_2", "label": "ATR(14)"}]
    assert all(line["label"] != "ATR(14)" for line in groups["价格"])


def test_custom_executes_next_open_and_replays_exactly(sample, definition):
    dates = sample["close"].index
    config = BacktestRequest(symbols=["TEST"], strategy="custom", custom_strategy=definition,
                             start=dates[0].date(), end=dates[-1].date())
    result = run_backtest(sample, config)
    assert result["trades"][0]["date"] == dates[2].strftime("%Y-%m-%d")
    assert result["trades"][0]["side"] == "BUY"
    changed = {name: frame.copy() for name, frame in sample.items()}
    for name in ("open", "high", "low", "close"):
        changed[name].iloc[80:] *= 2
    second = run_backtest(changed, config)
    assert result["curve"][:80] == second["curve"][:80]
    assert result["indicators"][:80] == second["indicators"][:80]
    assert [trade for trade in result["trades"] if trade["date"] < dates[80].strftime("%Y-%m-%d")] == [
        trade for trade in second["trades"] if trade["date"] < dates[80].strftime("%Y-%m-%d")
    ]
    snapshot = BacktestRequest.model_validate(result["config"])
    definition.name = "Later edit"
    assert run_backtest(sample, snapshot)["metrics"] == result["metrics"]
    assert snapshot.custom_strategy.name == "Test rules"


def test_comparison_includes_current_custom_snapshot(sample, definition):
    dates = sample["close"].index
    config = ComparisonRequest(symbols=["TEST"], custom_strategy=definition,
                               start=dates[0].date(), end=dates[-1].date())
    report = run_comparison(sample, config)
    assert report["summary"]["total"] == 13
    assert report["summary"]["failed"] == 0
    row = report["results"][-1]
    assert row["strategy"] == "custom"
    assert len(row["validation"]["checks"]) == 8
    assert all(check["status"] == "passed" for check in row["validation"]["checks"])
    assert row["metrics"] == run_backtest(sample, BacktestRequest.model_validate(row["config"]))["metrics"]
    assert row["config"]["custom_strategy"] == definition.model_dump()


def test_strategy_storage_survives_reopening_and_updates(tmp_path, definition):
    path = tmp_path / "research.sqlite3"
    store = ResearchStore(path)
    saved = store.save_strategy(definition)
    assert ResearchStore(path).list_strategies() == [saved]
    definition.name = "Updated"
    updated = store.save_strategy(definition, saved["id"])
    assert updated["id"] == saved["id"]
    assert ResearchStore(path).list_strategies() == [updated]
    assert store.delete_strategy(saved["id"])
    assert not store.delete_strategy(saved["id"])
    assert ResearchStore(path).list_strategies() == []


@pytest.mark.parametrize("mutation", ["empty", "too_many", "bad_kind", "no_indicator", "macd_periods", "code", "blank", "nan"])
def test_invalid_custom_definitions_are_rejected(definition, mutation):
    document = definition.model_dump()
    rule = document["entry"]["rules"][0]
    if mutation == "empty":
        document["entry"]["rules"] = []
    elif mutation == "too_many":
        document["entry"]["rules"] *= 7
    elif mutation == "bad_kind":
        rule["left"]["kind"] = "eval"
    elif mutation == "no_indicator":
        rule["right"] = {"type": "indicator"}
    elif mutation == "macd_periods":
        rule["left"] = {"kind": "macd_dif", "fast": 30, "slow": 12}
    elif mutation == "code":
        document["python"] = "print('not supported')"
    elif mutation == "blank":
        document["name"] = "   "
    else:
        rule["right"]["value"] = float("nan")
    with pytest.raises(ValidationError):
        CustomStrategy.model_validate(document)
