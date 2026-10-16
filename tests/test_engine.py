from datetime import date

import numpy as np
import pandas as pd
import pytest
import talib
from pydantic import ValidationError

from backend.engine import factors, run_backtest, simulate
from backend.models import HLC_STRATEGIES, STRATEGIES, BacktestRequest


def market(prices, *, opens=None, adjustment=None):
    prices = np.asarray(prices, dtype=float)
    dates = pd.bdate_range("2024-01-01", periods=len(prices))
    frame = pd.DataFrame({"TEST": prices}, index=dates)
    data = {field: frame.copy() for field in ("open", "high", "low", "close")}
    if opens is not None:
        data["open"]["TEST"] = opens
    data["cumadj"] = frame * 0 + 1
    if adjustment is not None:
        data["cumadj"]["TEST"] = adjustment
    data["amt"] = frame * 0 + 1_000_000
    data["num_trades"] = frame * 0 + 100
    return data


def config(data, *, strategy="sma", **kwargs):
    return BacktestRequest(
        symbols=["TEST"], start=data["close"].index[0].date(),
        end=data["close"].index[-1].date(), initial_cash=1000,
        commission_bps=0, slippage_bps=0, sell_tax_bps=0,
        strategy=strategy, fast=2, slow=3, **kwargs,
    )


def test_signal_executes_next_open_not_same_close():
    data = market([10, 11, 12, 13, 14, 15], opens=[10, 11, 12, 20, 14, 15])
    result = run_backtest(data, config(data))
    buy = result["trades"][0]
    assert buy["date"] == "2024-01-04"
    assert buy["price"] == 20
    assert result["curve"][2]["nav"] == 1
    assert result["curve"][3]["nav"] == pytest.approx(13 / 20)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_future_values_cannot_change_prior_results(strategy):
    prices = 20 + np.sin(np.arange(120) / 4) * 3
    data = market(prices)
    original = run_backtest(data, config(data, strategy=strategy, min_amount=500))
    changed = {name: frame.copy() for name, frame in data.items()}
    for name in ("close", "open", "high", "low", "amt", "cumadj"):
        changed[name].iloc[80:, 0] *= 3
    rerun = run_backtest(changed, config(changed, strategy=strategy, min_amount=500))
    assert original["curve"][:80] == rerun["curve"][:80]
    assert original["indicators"][:80] == rerun["indicators"][:80]
    assert [t for t in original["trades"] if t["date"] < original["curve"][80]["date"]] == [
        t for t in rerun["trades"] if t["date"] < original["curve"][80]["date"]
    ]


def test_amount_filter_uses_previous_day_only():
    data = market([10, 11, 12, 13, 14, 15])
    data["amt"].iloc[2, 0] = 0
    data["amt"].iloc[3, 0] = 2000
    result = run_backtest(data, config(data, min_amount=1000))
    assert result["trades"][0]["date"] == "2024-01-05"


def test_adjustment_preserves_split_returns():
    data = market([10, 11, 12, 13, 6.5, 7], adjustment=[1, 1, 1, 1, 2, 2])
    result = run_backtest(data, config(data))
    assert result["trades"][0]["price"] == 13
    assert result["curve"][4]["nav"] == pytest.approx(1)
    assert result["curve"][-1]["nav"] == pytest.approx(14 / 13)


def test_fees_slippage_and_realized_profit():
    data = market([10, 10, 12])
    settings = config(data).model_copy(update={
        "commission_bps": 10, "slippage_bps": 20, "sell_tax_bps": 30,
    })
    curve, trades, units = simulate(
        data["close"].index, np.array([[10], [10], [12.]]),
        np.array([[10], [10], [12.]]), np.array([[True], [True], [False]]),
        np.ones((3, 1), dtype=bool), settings, ["TEST"],
    )
    quantity = (1000 / 1.001) / 10.02
    expected = quantity * 11.976 * (1 - 0.004)
    assert trades[0]["price"] == pytest.approx(10.02)
    assert trades[1]["price"] == pytest.approx(11.976)
    assert curve[-1]["equity"] == pytest.approx(expected)
    assert trades[1]["pnl"] == pytest.approx(expected - 1000)
    assert units[0] == 0


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_talib_factors_and_missing_bars(strategy):
    prices = 30 + np.sin(np.arange(150) / 4) * 5
    prices[40:43] = np.nan
    settings = BacktestRequest(strategy=strategy)
    target, lines = factors(prices, settings, high=prices + 1, low=prices - 1)
    assert len(target) == len(prices)
    assert all(np.isnan(line[40:43]).all() for line in lines.values())
    assert all(np.isfinite(line[-1]) for line in lines.values())
    if strategy == "macd":
        dif, _, _ = talib.MACD(prices[np.isfinite(prices)], 12, 26, 9)
        np.testing.assert_allclose(lines["DIF"][np.isfinite(prices)], dif, equal_nan=True)


@pytest.mark.parametrize("strategy", ["ema", "kdj", "cci", "adx", "willr", "roc", "atr", "donchian"])
def test_new_factors_match_talib_with_custom_parameters(strategy):
    close = 30 + np.sin(np.arange(180) / 5) * 5
    high, low = close + 1.5, close - 0.7
    close[45:48] = np.nan
    settings = BacktestRequest(
        strategy=strategy, fast=5, slow=13, stoch_period=7, stoch_k=2, stoch_d=4,
        cci_period=10, adx_period=10, willr_period=10, roc_period=8,
        atr_period=10, atr_ma_period=17, atr_multiplier=1.3,
        donchian_entry=15, donchian_exit=6,
    )
    _, lines = factors(close, settings, high=high, low=low)
    valid = np.isfinite(close)
    c, h, l = close[valid], high[valid], low[valid]
    if strategy == "ema":
        expected = {"FAST": talib.EMA(c, 5), "SLOW": talib.EMA(c, 13)}
    elif strategy == "kdj":
        k, d = talib.STOCH(h, l, c, 7, 2, 0, 4, 0)
        expected = {"K": k, "D": d, "J": 3 * k - 2 * d}
    elif strategy == "cci":
        expected = {"CCI": talib.CCI(h, l, c, 10)}
    elif strategy == "adx":
        expected = {
            "ADX": talib.ADX(h, l, c, 10),
            "PLUS_DI": talib.PLUS_DI(h, l, c, 10),
            "MINUS_DI": talib.MINUS_DI(h, l, c, 10),
        }
    elif strategy == "willr":
        expected = {"WILLR": talib.WILLR(h, l, c, 10)}
    elif strategy == "roc":
        expected = {"ROC": (c / pd.Series(c).shift(8).to_numpy() - 1) * 100}
    elif strategy == "atr":
        middle, width = talib.EMA(c, 17), talib.ATR(h, l, c, 10) * 1.3
        expected = {"UPPER": middle + width, "MIDDLE": middle, "LOWER": middle - width}
    else:
        expected = {
            "UPPER": pd.Series(h).shift(1).rolling(15).max().to_numpy(),
            "LOWER": pd.Series(l).shift(1).rolling(6).min().to_numpy(),
        }
    assert set(lines) == set(expected)
    for key, reference in expected.items():
        np.testing.assert_allclose(lines[key][valid], reference, equal_nan=True)
        assert np.isnan(lines[key][~valid]).all()


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("prices", [[10.0, 11.0], [np.nan] * 30])
def test_empty_or_short_history_stays_flat(strategy, prices):
    close = np.asarray(prices)
    target, lines = factors(close, BacktestRequest(strategy=strategy), high=close + 1, low=close - 1)
    assert not target.any()
    assert all(len(line) == len(close) and np.isnan(line).all() for line in lines.values())


@pytest.mark.parametrize("strategy", sorted(HLC_STRATEGIES))
def test_high_low_gaps_preserve_state_without_fabricating_indicators(strategy):
    close = 30 + np.sin(np.arange(120) / 4) * 5
    high, low = close + 1, close - 1
    high[50], low[51], high[52] = np.nan, np.nan, close[52] - 1
    target, lines = factors(close, BacktestRequest(strategy=strategy), high=high, low=low)
    assert all(np.isnan(line[50:53]).all() for line in lines.values())
    assert np.all(target[50:53] == target[49])
    assert all(np.isfinite(line[-1]) for line in lines.values())


@pytest.mark.parametrize("strategy", sorted(HLC_STRATEGIES))
def test_high_low_strategies_reject_missing_required_inputs(strategy):
    with pytest.raises(ValueError, match="high、low"):
        factors(np.arange(10, 50, dtype=float), BacktestRequest(strategy=strategy))


@pytest.mark.parametrize("strategy", sorted(HLC_STRATEGIES))
def test_split_adjusts_high_low_and_close_consistently(strategy):
    data = market(30 + np.sin(np.arange(140) / 5) * 5)
    data["high"] += 1
    data["low"] -= 1
    split = {name: frame.copy() for name, frame in data.items()}
    for field in ("open", "high", "low", "close"):
        split[field].iloc[60:, 0] /= 2
    split["cumadj"].iloc[60:, 0] *= 2
    baseline = run_backtest(data, config(data, strategy=strategy))
    adjusted = run_backtest(split, config(split, strategy=strategy))
    assert baseline["indicators"] == adjusted["indicators"]
    assert baseline["curve"] == adjusted["curve"]
    assert baseline["trades"] == adjusted["trades"]


def test_donchian_excludes_current_high_and_executes_next_open():
    data = market([10, 11, 12, 13, 14, 15])
    data["high"] += 0.1
    data["low"] -= 0.1
    settings = config(data, strategy="donchian", donchian_entry=2, donchian_exit=2)
    result = run_backtest(data, settings)
    assert result["indicators"][1]["UPPER"] is None
    assert result["indicators"][2]["UPPER"] == pytest.approx(11.1)
    assert result["trades"][0]["date"] == "2024-01-04"
    assert result["trades"][0]["price"] == 13
    data["high"].iloc[2, 0] = 1000
    changed = run_backtest(data, settings)
    assert changed["indicators"][2]["UPPER"] == pytest.approx(11.1)
    assert changed["trades"][0] == result["trades"][0]


def test_invalid_high_low_are_explicitly_reported():
    data = market(np.arange(10, 50))
    data["high"].iloc[10, 0] = 0
    result = run_backtest(data, config(data, strategy="cci"))
    assert any("1 个缺失或不合法行情" in warning for warning in result["warnings"])


def test_warmup_uses_history_before_selected_start():
    data = market(np.arange(10, 40))
    settings = config(data).model_copy(update={"start": data["close"].index[20].date()})
    result = run_backtest(data, settings)
    assert result["trades"][0]["date"] == data["close"].index[20].strftime("%Y-%m-%d")


def test_missing_open_defers_trade_and_missing_close_keeps_valuation():
    data = market([10, 11, 12, 13, 14, np.nan, 16], opens=[10, 11, 12, np.nan, 14, np.nan, 16])
    result = run_backtest(data, config(data))
    assert result["trades"][0]["date"] == "2024-01-05"
    assert result["curve"][5]["nav"] == result["curve"][4]["nav"]
    assert len(result["warnings"]) > 4


def test_independent_equal_initial_sleeves():
    data = market([10, 11, 12, 13, 14, 15])
    for frame in data.values():
        frame["SECOND"] = frame["TEST"]
    settings = config(data).model_copy(update={"symbols": ["TEST", "SECOND"]})
    result = run_backtest(data, settings)
    assert len(result["trades"]) == 2
    assert all(trade["notional"] == 500 for trade in result["trades"])
    assert result["metrics"]["final_equity"] == pytest.approx(1000 * 15 / 13)


def test_no_trades_and_no_completed_trades_are_not_fake_statistics():
    data = market([10] * 20)
    result = run_backtest(data, config(data))
    assert result["metrics"]["sharpe"] is None
    assert result["metrics"]["win_rate"] is None
    assert result["metrics"]["total_return"] == 0
    assert result["metrics"]["max_drawdown"] == 0


def test_monthly_returns_compound_to_total_return():
    data = market(20 + np.sin(np.arange(100) / 5) * 3)
    result = run_backtest(data, config(data))
    compounded = np.prod([1 + point["return"] for point in result["monthly"]])
    assert compounded == pytest.approx(1 + result["metrics"]["total_return"])


def test_one_inactive_security_retains_its_cash_and_is_reported():
    data = market(np.arange(10, 40))
    for frame in data.values():
        frame["SECOND"] = np.nan
    settings = config(data).model_copy(update={"symbols": ["TEST", "SECOND"]})
    result = run_backtest(data, settings)
    assert result["curve"][-1]["cash"] == 500
    assert any("SECOND" in warning for warning in result["warnings"])


def test_entirely_missing_prices_are_rejected():
    data = market([np.nan] * 20)
    with pytest.raises(ValueError, match="没有有效开盘"):
        run_backtest(data, config(data))


@pytest.mark.parametrize("values", [
    {"fast": 26, "slow": 12},
    {"start": date(2025, 1, 2), "end": date(2025, 1, 1)},
    {"symbols": []}, {"symbols": ["TEST", "TEST"]},
    {"slippage_bps": -1}, {"initial_cash": float("nan")},
    {"strategy": "invalid"},
    {"strategy": "ema", "fast": 26, "slow": 12},
    {"strategy": "donchian", "donchian_entry": 10, "donchian_exit": 20},
    {"stoch_k": 0}, {"cci_buy": 10}, {"willr_sell": 20},
    {"atr_multiplier": 0}, {"roc_period": 1}, {"adx_threshold": 100},
])
def test_invalid_config_is_rejected(values):
    with pytest.raises(ValidationError):
        BacktestRequest(**values)
