import numpy as np
import pandas as pd

from .evaluation import correlation_summary, cross_section_correlation, number
from .harness_models import HarnessRequest
from .qlib_data import QlibPanel


def portfolio(panel: QlibPanel, values: np.ndarray, start, end, config: HarnessRequest,
              *, benchmark: bool = False) -> dict:
    indices = np.flatnonzero((panel.dates >= pd.Timestamp(start)) & (panel.dates <= pd.Timestamp(end)))
    if len(indices) < 60:
        raise ValueError("每个实验区间至少需要 60 个实际交易日")
    units, marks = np.zeros(len(panel.symbols)), np.zeros(len(panel.symbols))
    cash, fees, turnover = 1.0, 0.0, 0.0
    navs, points = [], []
    traded_days, unfilled = 0, 0
    commission, slip, tax = (getattr(config, key) / 10000 for key in ("commission_bps", "slippage_bps", "sell_tax_bps"))
    for day, index in enumerate(indices):
        opening, closing = panel.fields["open"][index], panel.fields["close"][index]
        tradable = np.isfinite(opening) & (opening > 0)
        marks[tradable] = opening[tradable]
        if day % config.horizon == 0 and index > 0:
            signal = index - 1
            scores = values[signal]
            eligible = panel.membership[signal] & np.isfinite(panel.fields["close"][signal]) & (panel.fields["close"][signal] > 0)
            if not benchmark:
                eligible &= np.isfinite(scores)
            pool = np.flatnonzero(eligible)
            # Selection uses only the prior close; missing next opens never trigger replacement selection.
            if len(pool) >= config.min_samples:
                selected = pool if benchmark else pool[np.argsort(-scores[pool], kind="stable")[:max(1, int(np.ceil(len(pool) * config.top_fraction)))]]
                equity = cash + float(units @ marks)
                frozen = float(units[~tradable] @ marks[~tradable])
                budget = max(0.0, equity - frozen) / len(selected)
                target = np.zeros(len(units))
                available = selected[tradable[selected]]
                target[available] = budget / opening[available]
                target[~tradable] = units[~tradable]
                unfilled += int((~tradable[selected]).sum() + ((units > 0) & ~tradable).sum())
                sales = np.maximum(0, units - target)
                gross = sales * np.where(tradable, opening * (1 - slip), 0)
                sell_fees = float(gross.sum() * (commission + tax))
                cash += float(gross.sum()) - sell_fees
                units -= sales
                buys = np.maximum(0, target - units)
                needed = buys * np.where(tradable, opening * (1 + slip), 0)
                total_needed = float(needed.sum() * (1 + commission))
                scale = min(1.0, max(0.0, cash) / total_needed) if total_needed > 0 else 0
                bought = float(needed.sum() * scale)
                buy_fees = bought * commission
                cash -= bought + buy_fees
                units += buys * scale
                fees += sell_fees + buy_fees
                turnover += float(gross.sum()) + bought
                traded_days += int(float(gross.sum()) + bought > 1e-12)
        valid = np.isfinite(closing) & (closing > 0)
        marks[valid] = closing[valid]
        equity = cash + float(units @ marks)
        if not np.isfinite(equity) or equity <= 0 or cash < -1e-8:
            raise ValueError("组合回测出现非有限资产、非正净值或负现金")
        navs.append(equity)
        points.append({"date": panel.dates[index].strftime("%Y-%m-%d"), "nav": equity})
    nav = np.array(navs)
    daily = nav / np.r_[1, nav[:-1]] - 1
    std = float(daily.std(ddof=1))
    drawdown = nav / np.maximum.accumulate(np.r_[1, nav])[1:] - 1
    for row, dd in zip(points, drawdown):
        row["drawdown"] = float(dd)
    return {
        "total_return": float(nav[-1] - 1), "annual_return": float(nav[-1] ** (252 / len(nav)) - 1),
        "sharpe": float(daily.mean() / std * np.sqrt(252)) if std > 1e-12 else None,
        "max_drawdown": float(drawdown.min()), "volatility": std * float(np.sqrt(252)),
        "fees": fees, "turnover": turnover, "traded_days": traded_days, "unfilled_orders": unfilled,
        "curve": points,
    }


def evaluate_expression(panel: QlibPanel, values: np.ndarray, start, end, config: HarnessRequest) -> dict:
    mask = (panel.dates >= pd.Timestamp(start)) & (panel.dates <= pd.Timestamp(end))
    factor = values[mask]
    opens = panel.fields["open"][mask]
    membership = panel.membership[mask]
    if membership.sum() == 0:
        raise ValueError("所选区间没有历史成分股观测")
    coverage = float((np.isfinite(factor) & membership).sum() / membership.sum())
    forward = np.full(opens.shape, np.nan)
    h = config.horizon
    if len(opens) > h + 1:
        entry, exit_price = opens[1:-h], opens[h + 1:]
        valid = np.isfinite(entry) & np.isfinite(exit_price) & (entry > 0) & (exit_price > 0)
        np.divide(exit_price, entry, out=forward[:-h - 1], where=valid)
        forward[:-h - 1] -= 1
    ic = cross_section_correlation(factor, forward, config.min_samples)
    rank_ic = cross_section_correlation(factor, forward, config.min_samples, rank=True)
    summary = correlation_summary(ic, rank_ic)
    result = portfolio(panel, values, start, end, config)
    result.update({
        **summary, "coverage": coverage, "days": int(mask.sum()),
        "daily_ic": [{"date": date.strftime("%Y-%m-%d"), "ic": number(a), "rank_ic": number(b)}
                     for date, a, b in zip(panel.dates[mask], ic, rank_ic)],
    })
    return result


def promotion_reason(candidate: dict, incumbent: dict, baseline_train: dict, config: HarnessRequest) -> tuple[bool, str]:
    train, validation = candidate["train"], candidate["validation"]
    previous = incumbent["validation"]
    if min(train["coverage"], validation["coverage"]) < config.min_coverage:
        return False, "拒绝：训练或开发验证覆盖率不足"
    if min(train["valid_days"], validation["valid_days"]) < 60:
        return False, "拒绝：有效 IC 日期不足 60"
    if any(row[key] is None for row in (train, validation, previous) for key in ("ic_mean", "icir")):
        return False, "拒绝：IC / ICIR 不可计算"
    if train["ic_mean"] < baseline_train["ic_mean"]:
        return False, "拒绝：训练 IC 低于预先固定基准"
    if validation["ic_mean"] < previous["ic_mean"] + config.min_ic_gain:
        return False, "拒绝：开发验证 IC 未达到晋升增量"
    if validation["icir"] < previous["icir"] + config.min_icir_gain:
        return False, "拒绝：开发验证 ICIR 未达到晋升增量"
    if validation["sharpe"] is None or previous["sharpe"] is None:
        return False, "拒绝：净收益夏普不可计算"
    if validation["sharpe"] < previous["sharpe"] - .1:
        return False, "拒绝：夏普恶化超过 0.1"
    if validation["max_drawdown"] < previous["max_drawdown"] - .05:
        return False, "拒绝：最大回撤恶化超过 5 个百分点"
    return True, "晋升：开发验证 IC 与 ICIR 双提升，且覆盖率、训练及收益风险护栏通过"
