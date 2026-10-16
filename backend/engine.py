import numpy as np
import pandas as pd
import talib

from .models import HLC_STRATEGIES, BacktestRequest


def valid_hlc(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    return (
        np.isfinite(high) & np.isfinite(low) & np.isfinite(close)
        & (low > 0) & (low <= close) & (close <= high)
    )


def factors(
    close: np.ndarray,
    config: BacktestRequest,
    *,
    high: np.ndarray | None = None,
    low: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Run TA-Lib on observed bars; missing bars neither reset nor create signals."""
    valid = np.isfinite(close) & (close > 0)
    if config.strategy in HLC_STRATEGIES:
        if high is None or low is None or high.shape != close.shape or low.shape != close.shape:
            raise ValueError("该策略需要与收盘价对齐的 high、low 行情")
        valid &= valid_hlc(high, low, close)
        highs = np.ascontiguousarray(high[valid], dtype=np.float64)
        lows = np.ascontiguousarray(low[valid], dtype=np.float64)
    values = np.ascontiguousarray(close[valid], dtype=np.float64)
    lines: dict[str, np.ndarray] = {}
    if config.strategy == "macd":
        dif, dea, hist = talib.MACD(
            values, fastperiod=config.fast, slowperiod=config.slow, signalperiod=config.signal
        )
        lines = {"DIF": dif, "DEA": dea, "MACD": hist * 2}
        buy, sell = dif > dea, dif <= dea
    elif config.strategy in ("sma", "ema"):
        average = talib.SMA if config.strategy == "sma" else talib.EMA
        fast = average(values, timeperiod=config.fast)
        slow = average(values, timeperiod=config.slow)
        lines = {"FAST": fast, "SLOW": slow}
        buy, sell = fast > slow, fast <= slow
    elif config.strategy == "rsi":
        rsi = talib.RSI(values, timeperiod=config.rsi_period)
        lines = {"RSI": rsi}
        buy, sell = rsi < config.rsi_buy, rsi > config.rsi_sell
    elif config.strategy == "boll":
        upper, middle, lower = talib.BBANDS(
            values, timeperiod=config.boll_period,
            nbdevup=config.boll_std, nbdevdn=config.boll_std, matype=0,
        )
        lines = {"UPPER": upper, "MIDDLE": middle, "LOWER": lower}
        buy, sell = values < lower, values >= middle
    elif config.strategy == "kdj":
        k, d = talib.STOCH(
            highs, lows, values, fastk_period=config.stoch_period,
            slowk_period=config.stoch_k, slowk_matype=0,
            slowd_period=config.stoch_d, slowd_matype=0,
        )
        lines = {"K": k, "D": d, "J": 3 * k - 2 * d}
        buy = (k > d) & (k < config.stoch_buy)
        sell = (k < d) | (k > config.stoch_sell)
    elif config.strategy == "cci":
        cci = talib.CCI(highs, lows, values, timeperiod=config.cci_period)
        lines = {"CCI": cci}
        buy, sell = cci < config.cci_buy, cci > config.cci_sell
    elif config.strategy == "adx":
        adx = talib.ADX(highs, lows, values, timeperiod=config.adx_period)
        plus = talib.PLUS_DI(highs, lows, values, timeperiod=config.adx_period)
        minus = talib.MINUS_DI(highs, lows, values, timeperiod=config.adx_period)
        lines = {"ADX": adx, "PLUS_DI": plus, "MINUS_DI": minus}
        buy = (adx >= config.adx_threshold) & (plus > minus)
        sell = (adx < config.adx_threshold) | (plus <= minus)
    elif config.strategy == "willr":
        willr = talib.WILLR(highs, lows, values, timeperiod=config.willr_period)
        lines = {"WILLR": willr}
        buy, sell = willr < config.willr_buy, willr > config.willr_sell
    elif config.strategy == "roc":
        roc = talib.ROC(values, timeperiod=config.roc_period)
        lines = {"ROC": roc}
        buy, sell = roc > config.roc_threshold, roc <= config.roc_threshold
    elif config.strategy == "atr":
        atr = talib.ATR(highs, lows, values, timeperiod=config.atr_period)
        middle = talib.EMA(values, timeperiod=config.atr_ma_period)
        upper = middle + config.atr_multiplier * atr
        lower = middle - config.atr_multiplier * atr
        lines = {"UPPER": upper, "MIDDLE": middle, "LOWER": lower}
        buy, sell = values > upper, values < lower
    elif config.strategy == "donchian":
        # Exclude the signal bar: today's close cannot break today's own high.
        upper, lower = np.full(len(values), np.nan), np.full(len(values), np.nan)
        upper[1:] = talib.MAX(highs, timeperiod=config.donchian_entry)[:-1]
        lower[1:] = talib.MIN(lows, timeperiod=config.donchian_exit)[:-1]
        lines = {"UPPER": upper, "LOWER": lower}
        buy, sell = values > upper, values < lower
    else:
        raise ValueError(f"不支持的策略：{config.strategy}")
    observed_target = np.zeros(len(values), dtype=bool)
    holding = False
    for i in range(len(values)):
        if sell[i]:
            holding = False
        elif buy[i]:
            holding = True
        observed_target[i] = holding
    target = pd.Series(np.nan, index=np.arange(len(close)))
    target.loc[valid] = observed_target.astype(float)
    desired = target.ffill().fillna(0).to_numpy(dtype=bool)
    full_lines = {}
    for name, line in lines.items():
        expanded = np.full(len(close), np.nan)
        expanded[valid] = line
        full_lines[name] = expanded
    return desired, full_lines


def simulate(
    dates: pd.DatetimeIndex,
    opens: np.ndarray,
    closes: np.ndarray,
    targets: np.ndarray,
    eligible: np.ndarray,
    config: BacktestRequest,
    symbols: list[str],
    *,
    benchmark: bool = False,
) -> tuple[list[dict], list[dict], np.ndarray]:
    count = len(symbols)
    cash = np.full(count, config.initial_cash / count)
    units = np.zeros(count)
    cost_basis = np.zeros(count)
    marks = np.zeros(count)
    commission = 0 if benchmark else config.commission_bps / 10000
    tax = 0 if benchmark else config.sell_tax_bps / 10000
    slip = 0 if benchmark else config.slippage_bps / 10000
    curve, trades = [], []
    for day, timestamp in enumerate(dates):
        for asset, symbol in enumerate(symbols):
            price = opens[day, asset]
            if not np.isfinite(price) or price <= 0:
                continue
            desired = True if benchmark else targets[day, asset]
            if units[asset] > 0 and not desired:
                execution = price * (1 - slip)
                gross = units[asset] * execution
                fees = gross * (commission + tax)
                cash[asset] += gross - fees
                trades.append({
                    "date": timestamp.strftime("%Y-%m-%d"), "symbol": symbol,
                    "side": "SELL", "price": float(execution), "units": float(units[asset]),
                    "notional": float(gross), "fees": float(fees),
                    "pnl": float(gross - fees - cost_basis[asset]),
                })
                units[asset] = cost_basis[asset] = 0
            elif units[asset] == 0 and desired and (benchmark or eligible[day, asset]):
                execution = price * (1 + slip)
                gross = cash[asset] / (1 + commission)
                fees = gross * commission
                units[asset] = gross / execution
                cost_basis[asset] = cash[asset]
                cash[asset] = 0
                trades.append({
                    "date": timestamp.strftime("%Y-%m-%d"), "symbol": symbol,
                    "side": "BUY", "price": float(execution), "units": float(units[asset]),
                    "notional": float(gross), "fees": float(fees), "pnl": None,
                })
            marks[asset] = price
        closing = closes[day]
        valid_close = np.isfinite(closing) & (closing > 0)
        marks[valid_close] = closing[valid_close]
        held_value = float(np.dot(units, marks))
        equity = float(cash.sum() + held_value)
        curve.append({
            "date": timestamp.strftime("%Y-%m-%d"), "equity": equity,
            "nav": equity / config.initial_cash, "cash": float(cash.sum()),
            "exposure": held_value / equity if equity else 0,
        })
    return curve, trades, units


def run_backtest(data: dict[str, pd.DataFrame], config: BacktestRequest) -> dict:
    dates = data["close"].index
    mask = (dates.date >= config.start) & (dates.date <= config.end)
    if mask.sum() < 2:
        raise ValueError("所选区间至少需要两个实际交易日")
    adj = data["cumadj"].where(data["cumadj"] > 0)
    # Anchor each security to its first observed factor, never to an end-date factor.
    anchor = adj.apply(lambda column: column.dropna().iloc[0] if column.notna().any() else np.nan)
    ratio = adj / anchor
    opens = (data["open"].where(data["open"] > 0) * ratio).to_numpy()
    closes = (data["close"].where(data["close"] > 0) * ratio).to_numpy()
    highs = (data["high"].where(data["high"] > 0) * ratio).to_numpy()
    lows = (data["low"].where(data["low"] > 0) * ratio).to_numpy()
    if not np.isfinite(opens[mask]).any():
        raise ValueError("所选证券在区间内没有有效开盘价格及复权因子")
    targets = np.zeros_like(closes, dtype=bool)
    detail_lines = {}
    for asset in range(len(config.symbols)):
        target, lines = factors(
            closes[:, asset], config, high=highs[:, asset], low=lows[:, asset],
        )
        targets[1:, asset] = target[:-1]
        if asset == 0:
            detail_lines = lines
    previous_amount = data["amt"].shift(1).to_numpy()
    eligible = np.ones_like(targets) if config.min_amount == 0 else (
        np.isfinite(previous_amount) & (previous_amount >= config.min_amount)
    )
    selected_dates = dates[mask]
    curve, trades, units = simulate(
        selected_dates, opens[mask], closes[mask], targets[mask], eligible[mask],
        config, config.symbols,
    )
    benchmark, _, _ = simulate(
        selected_dates, opens[mask], closes[mask], targets[mask], eligible[mask],
        config, config.symbols, benchmark=True,
    )
    nav = np.array([point["nav"] for point in curve])
    benchmark_nav = np.array([point["nav"] for point in benchmark])
    daily = nav / np.r_[1, nav[:-1]] - 1
    peaks = np.maximum.accumulate(np.r_[1, nav])[1:]
    drawdown = nav / peaks - 1
    std = float(np.std(daily, ddof=1))
    completed = [trade["pnl"] for trade in trades if trade["side"] == "SELL"]
    for i, point in enumerate(curve):
        point["benchmark"] = float(benchmark_nav[i])
        point["drawdown"] = float(drawdown[i])
    monthly_nav = pd.Series(nav, index=selected_dates)
    month_ends = monthly_nav.resample("ME").last()
    monthly = month_ends / month_ends.shift(1, fill_value=1) - 1
    detail = []
    for index in np.flatnonzero(mask):
        row: dict = {"date": dates[index].strftime("%Y-%m-%d")}
        for name, values in {"close": closes[:, 0], **detail_lines}.items():
            value = values[index]
            row[name] = float(value) if np.isfinite(value) else None
        detail.append(row)
    missing = int((~np.isfinite(closes[mask])).sum())
    warnings = [
        "研究模型：多头/空仓、可碎份额的复权单位；未模拟整手、涨跌停、真实停牌、退市处置及市场冲击。",
        "复权价 = 原始价格 × 当日 cumadj / 首个有效 cumadj；假设 cumadj 为乘法累积复权因子。",
        "每只证券分配等额初始资金、独立复投，不跨证券再平衡；期末持仓按收盘估值，不强制平仓。",
        "基准为同一证券池等初始资金买入持有（无费用），不是市场指数；固定税率不反映历史税制变化。",
    ]
    inactive = [
        symbol for asset, symbol in enumerate(config.symbols)
        if not np.isfinite(opens[mask, asset]).any()
    ]
    if inactive:
        warnings.append(f"以下证券区间内没有有效复权开盘价，子账户保留现金：{', '.join(inactive)}。")
    if missing:
        warnings.append(f"区间内有 {missing} 个缺失复权收盘值；缺失时使用当日有效开盘或最近可用价格估值。")
    if config.strategy in HLC_STRATEGIES:
        invalid_bars = int((~valid_hlc(highs[mask], lows[mask], closes[mask])).sum())
        if invalid_bars:
            warnings.append(f"该策略需要完整高低收盘价；区间内 {invalid_bars} 个缺失或不合法行情被跳过，不生成新信号。")
    counts = data["num_trades"].loc[mask]
    if counts.iloc[-1].isna().any():
        warnings.append("最后交易日存在 num_trades 缺失；成交笔数仅作数据检查，不用于开盘撮合。")
    if not trades:
        warnings.append("当前参数未产生交易，可能由信号、预热长度、流动性门槛或数据缺失导致。")
    return {
        "config": config.model_dump(mode="json"),
        "period": {"start": curve[0]["date"], "end": curve[-1]["date"], "days": len(curve)},
        "metrics": {
            "total_return": float(nav[-1] - 1),
            "annual_return": float(nav[-1] ** (252 / len(nav)) - 1),
            "benchmark_return": float(benchmark_nav[-1] - 1),
            "excess_return": float(nav[-1] - benchmark_nav[-1]),
            "max_drawdown": float(drawdown.min()),
            "sharpe": float(daily.mean() / std * np.sqrt(252)) if std > 1e-12 else None,
            "volatility": std * np.sqrt(252),
            "win_rate": sum(pnl > 0 for pnl in completed) / len(completed) if completed else None,
            "trade_count": len(trades), "closed_trades": len(completed),
            "fees": float(sum(trade["fees"] for trade in trades)),
            "final_equity": curve[-1]["equity"],
            "average_exposure": float(np.mean([point["exposure"] for point in curve])),
            "open_positions": int((units > 0).sum()),
        },
        "curve": curve, "trades": trades,
        "monthly": [{"month": date.strftime("%Y-%m"), "return": float(value)} for date, value in monthly.items()],
        "indicator_symbol": config.symbols[0], "indicators": detail, "warnings": warnings,
    }
