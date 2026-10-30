from time import perf_counter

import numpy as np
import pandas as pd

from .indicators import PRICE_KINDS, calculate_indicator, indicator_label
from .models import FactorEvaluationRequest


def cross_section_correlation(left: np.ndarray, right: np.ndarray, minimum: int, *, rank: bool = False) -> np.ndarray:
    valid = np.isfinite(left) & np.isfinite(right)
    a, b = np.where(valid, left, np.nan), np.where(valid, right, np.nan)
    if rank:
        a = pd.DataFrame(a).rank(axis=1, method="average").to_numpy()
        b = pd.DataFrame(b).rank(axis=1, method="average").to_numpy()
    count = valid.sum(axis=1)
    denominator = np.maximum(count, 1)
    a = np.where(valid, a - np.nansum(a, axis=1)[:, None] / denominator[:, None], 0)
    b = np.where(valid, b - np.nansum(b, axis=1)[:, None] / denominator[:, None], 0)
    norm = np.sqrt(np.sum(a * a, axis=1) * np.sum(b * b, axis=1))
    result = np.full(len(left), np.nan)
    np.divide(np.sum(a * b, axis=1), norm, out=result, where=(count >= minimum) & (norm > 0))
    return np.clip(result, -1, 1)


def number(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def correlation_summary(ic: np.ndarray, rank_ic: np.ndarray) -> dict:
    def statistics(values: np.ndarray) -> tuple[float | None, float | None, float | None, float | None, int]:
        observed = values[np.isfinite(values)]
        if not len(observed):
            return None, None, None, None, 0
        mean = float(observed.mean())
        std = float(observed.std(ddof=1)) if len(observed) > 1 else None
        ratio = mean / std if std is not None and std > 1e-12 else None
        return mean, std, ratio, float(np.mean(observed > 0)), len(observed)

    mean, std, ratio, positive, days = statistics(ic)
    rank_mean, rank_std, rank_ratio, rank_positive, rank_days = statistics(rank_ic)
    return {
        "ic_mean": mean, "ic_std": std, "icir": ratio, "ic_positive_rate": positive,
        "valid_days": days, "rank_ic_mean": rank_mean, "rank_ic_std": rank_std,
        "rank_icir": rank_ratio, "rank_ic_positive_rate": rank_positive, "rank_valid_days": rank_days,
    }


def quantile_analysis(values: np.ndarray, returns: np.ndarray, dates: pd.DatetimeIndex,
                      horizon: int, groups: int, minimum: int) -> dict:
    nav = np.ones(groups)
    observations: list[np.ndarray] = []
    curve = []
    spread_sum = 0.0
    skipped = 0
    for index in range(0, len(dates) - horizon - 1, horizon):
        valid = np.isfinite(values[index]) & np.isfinite(returns[index])
        if valid.sum() < minimum:
            skipped += 1
            continue
        factor, forward = values[index, valid], returns[index, valid]
        ranks = pd.Series(factor).rank(method="average").to_numpy()
        bins = np.minimum(np.ceil(ranks / len(ranks) * groups).astype(int) - 1, groups - 1)
        if len(np.unique(bins)) != groups:
            skipped += 1
            continue
        group_returns = np.array([forward[bins == group].mean() for group in range(groups)])
        nav *= 1 + group_returns
        observations.append(group_returns)
        spread = float(group_returns[-1] - group_returns[0])
        spread_sum += spread
        curve.append({
            "signal_date": dates[index].strftime("%Y-%m-%d"),
            "entry_date": dates[index + 1].strftime("%Y-%m-%d"),
            "date": dates[index + horizon + 1].strftime("%Y-%m-%d"),
            "returns": group_returns.tolist(), "nav": nav.tolist(),
            "spread": spread, "cumulative_spread": spread_sum, "samples": int(valid.sum()),
        })
    means = np.mean(observations, axis=0) if observations else np.full(groups, np.nan)
    monotonicity = cross_section_correlation(
        np.arange(groups, dtype=float)[None, :], means[None, :], groups, rank=True,
    )[0]
    return {
        "periods": len(curve), "skipped_periods": skipped, "curve": curve,
        "groups": [{"group": index + 1, "mean_return": number(means[index]),
                    "total_return": float(nav[index] - 1) if observations else None} for index in range(groups)],
        "mean_spread": float(np.mean([row["spread"] for row in curve])) if curve else None,
        "monotonicity": number(monotonicity),
    }


def run_factor_evaluation(data: dict[str, pd.DataFrame], request: FactorEvaluationRequest) -> dict:
    started = perf_counter()
    config = request.config
    if len(config.symbols) < request.min_samples:
        raise ValueError(f"股票池仅有 {len(config.symbols)} 只证券，少于最少截面样本数 {request.min_samples}")
    history = {field: frame.loc[:pd.Timestamp(config.end), config.symbols] for field, frame in data.items()}
    dates = history["close"].index
    mask = dates.date >= config.start
    evaluation_dates = dates[mask]
    if len(evaluation_dates) < request.primary_horizon + 2:
        raise ValueError("所选区间不足以生成主评价周期的次日开盘收益标签")
    adjustment = history["cumadj"].where(np.isfinite(history["cumadj"]) & (history["cumadj"] > 0))
    anchor = adjustment.apply(lambda column: column.dropna().iloc[0] if column.notna().any() else np.nan)
    ratio = adjustment / anchor
    prices = {field: (history[field].where(history[field] > 0) * ratio).to_numpy()
              for field in ("open", "close", "high", "low")}
    opens = prices["open"][mask]
    eligible = np.ones(opens.shape, dtype=bool)
    if config.min_amount > 0:
        amount = history["amt"].to_numpy()[mask]
        eligible &= np.isfinite(amount) & (amount >= config.min_amount)
    forward_returns = {}
    for horizon in sorted(request.horizons):
        forward = np.full(opens.shape, np.nan)
        if len(opens) > horizon + 1:
            entry, exit_price = opens[1:-horizon], opens[horizon + 1:]
            valid = np.isfinite(entry) & np.isfinite(exit_price) & (entry > 0) & (exit_price > 0)
            np.divide(exit_price, entry, out=forward[:len(entry)], where=valid)
            forward[:len(entry)] -= 1
        forward_returns[horizon] = forward
    results, factor_arrays = [], []
    for index, spec in enumerate(request.factors):
        values = np.column_stack([
            calculate_indicator(spec, prices["close"][:, column], prices["high"][:, column], prices["low"][:, column])
            for column in range(len(config.symbols))
        ])
        transform = "原始指标值"
        if request.normalization == "relative_price" and spec.kind != "close":
            if spec.kind in PRICE_KINDS or spec.kind.startswith("macd_") or spec.kind == "atr":
                values = np.divide(values, prices["close"], out=np.full_like(values, np.nan),
                                   where=np.isfinite(prices["close"]) & (prices["close"] > 0))
                if spec.kind in PRICE_KINDS:
                    values -= 1
                transform = "指标 / 复权收盘 - 1" if spec.kind in PRICE_KINDS else "指标 / 复权收盘"
        values = np.where(eligible & np.isfinite(values[mask]), values[mask], np.nan)
        factor_arrays.append(values)
        coverage = np.isfinite(values).mean(axis=1)
        horizons, primary_ic, primary_rank = [], None, None
        for horizon, forward in forward_returns.items():
            ic = cross_section_correlation(values, forward, request.min_samples)
            rank_ic = cross_section_correlation(values, forward, request.min_samples, rank=True)
            horizons.append({"horizon": horizon, **correlation_summary(ic, rank_ic)})
            if horizon == request.primary_horizon:
                primary_ic, primary_rank = ic, rank_ic
        assert primary_ic is not None and primary_rank is not None
        primary_returns = forward_returns[request.primary_horizon]
        sample_counts = (np.isfinite(values) & np.isfinite(primary_returns)).sum(axis=1)
        primary = next(row for row in horizons if row["horizon"] == request.primary_horizon)
        warnings = []
        if primary["valid_days"] < 20:
            warnings.append("有效 IC 日期少于 20 个，统计稳定性不足；空值不代表零相关。")
        if spec.kind == "close" or (request.normalization == "raw" and (
            spec.kind in PRICE_KINDS or spec.kind.startswith("macd_") or spec.kind == "atr"
        )):
            warnings.append("原始价格量纲因子受股价及复权锚点影响，不具备天然的跨证券可比性。")
        quantiles = quantile_analysis(values, primary_returns, evaluation_dates, request.primary_horizon,
                                      request.groups, request.min_samples)
        if quantiles["skipped_periods"]:
            warnings.append(f"分层跳过 {quantiles['skipped_periods']} 期：样本不足或并列值导致空层；不拆分并列值。")
        if not quantiles["periods"]:
            warnings.append("没有可用分层期，不能将空结果解读为零收益。")
        results.append({
            "id": f"factor_{index + 1}", "label": indicator_label(spec), "spec": spec.model_dump(),
            "transform": transform, "coverage": float(coverage.mean()), "metrics": primary,
            "horizons": horizons, "warnings": warnings, "quantiles": quantiles,
            "daily": [{"date": date.strftime("%Y-%m-%d"), "ic": number(primary_ic[i]),
                       "rank_ic": number(primary_rank[i]), "coverage": float(coverage[i]),
                       "samples": int(sample_counts[i])} for i, date in enumerate(evaluation_dates)],
        })
    correlations = []
    for i, left in enumerate(factor_arrays):
        for j in range(i, len(factor_arrays)):
            daily = cross_section_correlation(left, factor_arrays[j], request.min_samples, rank=True)
            finite = daily[np.isfinite(daily)]
            correlations.append({
                "left": results[i]["id"], "right": results[j]["id"],
                "value": float(finite.mean()) if len(finite) else None, "days": len(finite),
            })
    return {
        "request": request.model_dump(mode="json"),
        "period": {"start": evaluation_dates[0].strftime("%Y-%m-%d"),
                   "end": evaluation_dates[-1].strftime("%Y-%m-%d"), "days": len(evaluation_dates)},
        "results": results, "correlations": correlations, "elapsed_ms": round((perf_counter() - started) * 1000),
        "warnings": [
            "信号为 T 日收盘指标，H 日收益 = 复权开盘价(T+1+H) / 复权开盘价(T+1) - 1；不使用区间外行情补全尾部标签。",
            "IC / Rank IC 为逐日横截面 Pearson / 平均秩 Spearman 相关；常数截面或样本不足记为空值。",
            "ICIR = 逐日 IC 均值 / 样本标准差，不年化；H>1 的日频标签重叠，不能据此作独立样本显著性推断。",
            "分层按因子值从低到高，固定正向 G最高−G最低，不根据全样本表现自动翻转方向。每 H 日调仓、层内等权，使用不重叠的开盘到开盘收益。",
            "分层净值按各层逐期复利；多空差为每期高低层收益差，累计差为算术累加，非可交易多空组合净值。",
            "分层使用有完整未来开盘标签的共同样本；未来缺失/退市样本排除可能引入选择偏差，不能视为实盘回测。",
            "未扣佣金、税费、滑点、借券及冲击成本，未做行业/市值中性化；不提供自动择优打分或显著性认证。",
            "覆盖率以全部所选证券×区间交易日为分母，含预热不足和缺失值；成交额门槛使用信号日已知成交额。",
            "因子相关性为共同有效样本的逐日 Rank 相关均值，不是 IC 序列之间的相关性。",
            "使用区间前的全部可用历史预热，复权锚点为各证券首个有效复权因子；跨缺失行情按已观测价格序列计算指标。",
        ],
    }
