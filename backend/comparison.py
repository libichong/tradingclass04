import logging
from time import perf_counter

import numpy as np
import pandas as pd

from .engine import run_backtest
from .models import STRATEGIES, BacktestRequest, ComparisonRequest

logger = logging.getLogger("uvicorn.error")


def equal_number(left, right) -> bool:
    if left is None or right is None:
        return left is right
    return bool(np.isclose(left, right, rtol=1e-9, atol=1e-7))


def equal_records(left: list[dict], right: list[dict]) -> bool:
    if len(left) != len(right):
        return False
    for actual, expected in zip(left, right):
        if actual.keys() != expected.keys():
            return False
        for key, value in actual.items():
            other = expected[key]
            if isinstance(value, str) or isinstance(other, str):
                if value != other:
                    return False
            elif not equal_number(value, other):
                return False
    return True


def ledger_consistent(result: dict, data: dict[str, pd.DataFrame], config: BacktestRequest) -> bool:
    cash = {symbol: config.initial_cash / len(config.symbols) for symbol in config.symbols}
    units = dict.fromkeys(config.symbols, 0.0)
    basis = dict.fromkeys(config.symbols, 0.0)
    valid = True
    dates = {point["date"] for point in result["curve"]}
    last_date = ""
    for trade in result["trades"]:
        symbol = trade["symbol"]
        if symbol not in cash or trade["side"] not in ("BUY", "SELL"):
            return False
        valid &= trade["date"] in dates and trade["date"] >= last_date
        last_date = trade["date"]
        gross, fee = trade["notional"], trade["fees"]
        valid &= all(np.isfinite(trade[key]) and trade[key] > 0 for key in ("units", "price", "notional"))
        valid &= equal_number(gross, trade["units"] * trade["price"])
        rate = config.commission_bps + (config.sell_tax_bps if trade["side"] == "SELL" else 0)
        valid &= equal_number(fee, gross * rate / 10000)
        if trade["side"] == "BUY":
            valid &= units[symbol] == 0 and trade["pnl"] is None
            cash[symbol] -= gross + fee
            units[symbol] = trade["units"]
            basis[symbol] = gross + fee
        else:
            valid &= equal_number(units[symbol], trade["units"])
            valid &= equal_number(trade["pnl"], gross - fee - basis[symbol])
            cash[symbol] += gross - fee
            units[symbol] = basis[symbol] = 0
        valid &= cash[symbol] >= -max(1e-7, config.initial_cash * 1e-9)
    final_equity = sum(cash.values())
    for symbol, quantity in units.items():
        if not quantity:
            continue
        adjustment = data["cumadj"][symbol]
        adjustment = adjustment.where(np.isfinite(adjustment) & (adjustment > 0))
        anchor = adjustment.dropna().iloc[0]
        close, opening = data["close"][symbol], data["open"][symbol]
        raw = close.where(np.isfinite(close) & (close > 0)).combine_first(
            opening.where(np.isfinite(opening) & (opening > 0))
        )
        marks = raw * adjustment / anchor
        marks = marks.loc[(marks.index.date >= config.start) & (marks.index.date <= config.end)]
        marks = marks[np.isfinite(marks) & (marks > 0)]
        if marks.empty:
            return False
        final_equity += quantity * marks.iloc[-1]
    return bool(
        valid
        and equal_number(sum(cash.values()), result["curve"][-1]["cash"])
        and equal_number(final_equity, result["metrics"]["final_equity"])
        and sum(quantity > 0 for quantity in units.values()) == result["metrics"]["open_positions"]
    )


def validate_backtest(
    result: dict, data: dict[str, pd.DataFrame], config: BacktestRequest, benchmark: list[dict],
) -> dict:
    checks = []

    def check(identifier: str, label: str, passed: bool, detail: str):
        checks.append({
            "id": identifier, "label": label,
            "status": "passed" if passed else "failed", "detail": detail,
        })

    expected_dates = data["close"].index
    expected_dates = expected_dates[
        (expected_dates.date >= config.start) & (expected_dates.date <= config.end)
    ]
    curve, metrics, trades = result["curve"], result["metrics"], result["trades"]
    dates = [point["date"] for point in curve]
    check("coverage", "交易日与区间对齐", (
        dates == expected_dates.strftime("%Y-%m-%d").tolist()
        and result["period"] == {"start": dates[0], "end": dates[-1], "days": len(dates)}
    ), f"实际 {len(dates)} 个交易日；与所选证券池共用同一交易日历。")
    finite = all(
        np.isfinite(value)
        for point in curve for key, value in point.items() if key != "date"
    ) and all(
        (key in ("sharpe", "win_rate")) if value is None else np.isfinite(value)
        for key, value in metrics.items()
    )
    finite &= all(
        point["nav"] > 0 and point["cash"] >= -1e-7 and -1e-9 <= point["exposure"] <= 1 + 1e-9
        for point in curve
    )
    check("finite", "数值与仓位边界", bool(finite), "净值、资金、指标须有限；仓位介于 0–100%，空统计保留 null。")
    nav = np.array([point["nav"] for point in curve])
    returns = nav / np.r_[1, nav[:-1]] - 1
    std = float(np.std(returns, ddof=1))
    drawdown = nav / np.maximum.accumulate(np.r_[1, nav])[1:] - 1
    completed = [trade["pnl"] for trade in trades if trade["side"] == "SELL"]
    expected_metrics = {
        "total_return": nav[-1] - 1,
        "annual_return": nav[-1] ** (252 / len(nav)) - 1,
        "benchmark_return": curve[-1]["benchmark"] - 1,
        "excess_return": nav[-1] - curve[-1]["benchmark"],
        "max_drawdown": drawdown.min(),
        "sharpe": returns.mean() / std * np.sqrt(252) if std > 1e-12 else None,
        "volatility": std * np.sqrt(252),
        "win_rate": sum(pnl > 0 for pnl in completed) / len(completed) if completed else None,
        "trade_count": len(trades), "closed_trades": len(completed),
        "fees": sum(trade["fees"] for trade in trades),
        "final_equity": nav[-1] * config.initial_cash,
        "average_exposure": np.mean([point["exposure"] for point in curve]),
    }
    metric_match = all(equal_number(metrics[key], value) for key, value in expected_metrics.items())
    metric_match &= np.allclose([point["drawdown"] for point in curve], drawdown, rtol=1e-9, atol=1e-7)
    metric_match &= all(equal_number(point["equity"], point["nav"] * config.initial_cash) for point in curve)
    check("metrics", "收益风险指标复算", bool(metric_match), "从逐日净值和成交记录重新核对收益、回撤、夏普、波动、胜率、费用等指标。")
    check("ledger", "交易账本与资产守恒", ledger_consistent(result, data, config),
          "核对买卖次序、数量、成交金额、佣税、已实现盈亏、剩余现金与期末持仓估值。")
    month_ends = pd.Series(nav, index=pd.to_datetime(dates)).resample("ME").last()
    month_returns = month_ends / month_ends.shift(1, fill_value=1) - 1
    expected_months = [
        {"month": timestamp.strftime("%Y-%m"), "return": float(value)}
        for timestamp, value in month_returns.items()
    ]
    check("monthly", "月度收益一致性", equal_records(result["monthly"], expected_months),
          "逐月收益独立复算，首月以初始资金为起点；首尾月可能不足整月。")
    check("benchmark", "买入持有基准一致", equal_records(
        [{"date": point["date"], "nav": point["benchmark"]} for point in curve], benchmark,
    ), "所有策略使用同证券池、等初始资金、无费用的相同买入持有基准。")
    prefix_count = max(2, len(expected_dates) // 2)
    cutoff = expected_dates[prefix_count - 1]
    prefix_open = data["open"].loc[expected_dates[:prefix_count]].to_numpy()
    prefix_adj = data["cumadj"].loc[expected_dates[:prefix_count]].to_numpy()
    has_open = np.any(np.isfinite(prefix_open) & (prefix_open > 0) & np.isfinite(prefix_adj) & (prefix_adj > 0))
    if prefix_count >= len(expected_dates) or not has_open:
        checks.append({
            "id": "prefix", "label": "历史前缀重放", "status": "skipped",
            "detail": "区间不足 3 个交易日，或前半段没有有效开盘价；不能完成前缀验证。",
        })
    else:
        prefix_data = {name: frame.loc[:cutoff] for name, frame in data.items()}
        prefix_config = BacktestRequest.model_validate({**config.model_dump(), "end": cutoff.date()})
        replay = run_backtest(prefix_data, prefix_config)
        before_cutoff = [trade for trade in trades if trade["date"] <= cutoff.strftime("%Y-%m-%d")]
        passed = (
            equal_records(curve[:prefix_count], replay["curve"])
            and equal_records(before_cutoff, replay["trades"])
            and equal_records(result["indicators"][:prefix_count], replay["indicators"])
        )
        check("prefix", "历史前缀重放", passed,
              f"截断数据至 {cutoff:%Y-%m-%d} 后重新回测，核对前 {prefix_count} 日净值、交易及首只证券指标。")
    checks.append({
        "id": "activity", "label": "有效交易样本",
        "status": "passed" if trades else "skipped",
        "detail": f"{len(trades)} 笔成交、{len(completed)} 笔已平仓；无交易不代表策略有效。",
    })
    status = "failed" if any(item["status"] == "failed" for item in checks) else (
        "warning" if any(item["status"] == "skipped" for item in checks) else "passed"
    )
    if status == "failed":
        logger.warning("Comparison validation failed for %s: %s", config.strategy,
                       [item["id"] for item in checks if item["status"] == "failed"])
    return {"status": status, "checks": checks}


def run_comparison(data: dict[str, pd.DataFrame], config: ComparisonRequest) -> dict:
    started = perf_counter()
    rows = []
    benchmark = None
    period = None
    candidates = (*STRATEGIES, "custom") if config.custom_strategy else STRATEGIES
    for strategy in candidates:
        strategy_config = BacktestRequest.model_validate({**config.model_dump(), "strategy": strategy})
        result = run_backtest(data, strategy_config)
        if benchmark is None:
            benchmark = [{"date": point["date"], "nav": point["benchmark"]} for point in result["curve"]]
            period = result["period"]
        validation = validate_backtest(result, data, strategy_config, benchmark)
        rows.append({
            "strategy": strategy, "config": result["config"], "metrics": result["metrics"],
            "curve": [{key: point[key] for key in ("date", "nav", "drawdown")} for point in result["curve"]],
            "monthly": result["monthly"], "warnings": result["warnings"], "validation": validation,
        })
    return {
        "config": config.model_dump(mode="json"), "period": period, "benchmark": benchmark, "results": rows,
        "summary": {
            "total": len(rows),
            "passed": sum(row["validation"]["status"] == "passed" for row in rows),
            "warning": sum(row["validation"]["status"] == "warning" for row in rows),
            "failed": sum(row["validation"]["status"] == "failed" for row in rows),
            "elapsed_ms": round((perf_counter() - started) * 1000),
        },
        "validation_note": "验证通过仅代表本次计算一致性检查通过；单次前缀重放不是全面无前视证明，也不是样本外盈利或实盘可交易性验证。",
    }
