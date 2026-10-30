import numpy as np
import talib

from .models import CustomStrategy, IndicatorSpec, RuleGroup

PRICE_KINDS = {"close", "sma", "ema", "boll_upper", "boll_middle", "boll_lower", "donchian_upper", "donchian_lower"}
HLC_KINDS = {"k", "d", "j", "cci", "adx", "plus_di", "minus_di", "willr", "atr", "donchian_upper", "donchian_lower"}


def valid_hlc(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    return np.isfinite(high) & np.isfinite(low) & np.isfinite(close) & (low > 0) & (low <= close) & (close <= high)


def indicator_label(spec: IndicatorSpec) -> str:
    if spec.kind == "close":
        return "CLOSE"
    if spec.kind.startswith("macd_"):
        return f"{spec.kind.upper()}({spec.fast},{spec.slow},{spec.signal})"
    if spec.kind in ("k", "d", "j"):
        return f"{spec.kind.upper()}({spec.period},{spec.k},{spec.d})"
    if spec.kind.startswith("boll_"):
        return f"{spec.kind.upper()}({spec.period},{spec.width:g})"
    return f"{spec.kind.upper()}({spec.period})"


def custom_catalog(definition: CustomStrategy) -> list[tuple[str, IndicatorSpec]]:
    specs: dict[str, IndicatorSpec] = {}
    for group in (definition.entry, definition.exit):
        for rule in group.rules:
            specs.setdefault(indicator_label(rule.left), rule.left)
            if rule.right.type == "indicator" and rule.right.indicator is not None:
                specs.setdefault(indicator_label(rule.right.indicator), rule.right.indicator)
    return [(f"indicator_{index}", spec) for index, spec in enumerate(specs.values(), 1)]


def custom_groups(definition: CustomStrategy) -> list[dict]:
    groups = {"价格": [{"key": "close", "label": "复权收盘"}]}
    for key, spec in custom_catalog(definition):
        group = "价格" if spec.kind in PRICE_KINDS else (
            "MACD" if spec.kind.startswith("macd_") else "KDJ" if spec.kind in ("k", "d", "j")
            else "ADX / DI" if spec.kind in ("adx", "plus_di", "minus_di") else spec.kind.upper()
        )
        groups.setdefault(group, []).append({"key": key, "label": indicator_label(spec)})
    return [{"name": name, "lines": lines} for name, lines in groups.items()]


def calculate_indicator(spec: IndicatorSpec, close: np.ndarray, high: np.ndarray, low: np.ndarray) -> np.ndarray:
    valid = np.isfinite(close) & (close > 0)
    if spec.kind in HLC_KINDS:
        valid &= valid_hlc(high, low, close)
    c, h, l = (np.ascontiguousarray(array[valid], dtype=np.float64) for array in (close, high, low))
    kind = spec.kind
    if kind == "close":
        values = c
    elif kind in ("sma", "ema", "rsi", "roc"):
        fn = {"sma": talib.SMA, "ema": talib.EMA, "rsi": talib.RSI, "roc": talib.ROC}[kind]
        values = fn(c, timeperiod=spec.period)
    elif kind.startswith("macd_"):
        dif, dea, hist = talib.MACD(c, spec.fast, spec.slow, spec.signal)
        values = {"macd_dif": dif, "macd_dea": dea, "macd_hist": hist * 2}[kind]
    elif kind in ("k", "d", "j"):
        k, d = talib.STOCH(h, l, c, spec.period, spec.k, 0, spec.d, 0)
        values = {"k": k, "d": d, "j": 3 * k - 2 * d}[kind]
    elif kind in ("cci", "adx", "plus_di", "minus_di", "willr", "atr"):
        fn = {"cci": talib.CCI, "adx": talib.ADX, "plus_di": talib.PLUS_DI,
              "minus_di": talib.MINUS_DI, "willr": talib.WILLR, "atr": talib.ATR}[kind]
        values = fn(h, l, c, timeperiod=spec.period)
    elif kind.startswith("boll_"):
        upper, middle, lower = talib.BBANDS(c, spec.period, spec.width, spec.width, 0)
        values = {"boll_upper": upper, "boll_middle": middle, "boll_lower": lower}[kind]
    else:
        fn = talib.MAX if kind == "donchian_upper" else talib.MIN
        observed = fn(h if kind == "donchian_upper" else l, timeperiod=spec.period)
        values = np.full(len(c), np.nan)
        values[1:] = observed[:-1]
    result = np.full(len(close), np.nan)
    result[valid] = values
    return result


def evaluate_group(group: RuleGroup, lines: dict[str, np.ndarray]) -> np.ndarray:
    conditions = []
    for rule in group.rules:
        left = lines[indicator_label(rule.left)]
        right = lines[indicator_label(rule.right.indicator)] if rule.right.type == "indicator" and rule.right.indicator else np.full(len(left), rule.right.value)
        valid = np.isfinite(left) & np.isfinite(right)
        if rule.operator in ("cross_above", "cross_below"):
            previous_left, previous_right = np.r_[np.nan, left[:-1]], np.r_[np.nan, right[:-1]]
            valid &= np.isfinite(previous_left) & np.isfinite(previous_right)
            condition = ((left > right) & (previous_left <= previous_right)) if rule.operator == "cross_above" else ((left < right) & (previous_left >= previous_right))
        else:
            condition = {"gt": np.greater, "gte": np.greater_equal, "lt": np.less, "lte": np.less_equal}[rule.operator](left, right)
        conditions.append(valid & condition)
    return np.logical_and.reduce(conditions) if group.mode == "all" else np.logical_or.reduce(conditions)


def custom_factors(close: np.ndarray, high: np.ndarray, low: np.ndarray, definition: CustomStrategy):
    catalog = custom_catalog(definition)
    values = {indicator_label(spec): calculate_indicator(spec, close, high, low) for _, spec in catalog}
    buy, sell = evaluate_group(definition.entry, values), evaluate_group(definition.exit, values)
    targets = np.zeros(len(close), dtype=bool)
    holding = False
    for i in range(len(close)):
        if sell[i]:
            holding = False
        elif buy[i]:
            holding = True
        targets[i] = holding
    return targets, {key: values[indicator_label(spec)] for key, spec in catalog}
