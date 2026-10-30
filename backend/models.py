from datetime import date
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

Strategy = Literal[
    "macd", "rsi", "sma", "boll", "ema", "kdj", "cci", "adx",
    "willr", "roc", "atr", "donchian", "custom",
]
STRATEGIES = tuple(item for item in get_args(Strategy) if item != "custom")
HLC_STRATEGIES = frozenset({"kdj", "cci", "adx", "willr", "atr", "donchian"})
UniverseId = Literal["hs300", "zz500", "zz1000", "sz50", "custom"]
IndicatorKind = Literal[
    "close", "sma", "ema", "rsi", "macd_dif", "macd_dea", "macd_hist",
    "k", "d", "j", "cci", "adx", "plus_di", "minus_di", "willr", "roc", "atr",
    "boll_upper", "boll_middle", "boll_lower", "donchian_upper", "donchian_lower",
]


class IndicatorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: IndicatorKind
    period: int = Field(default=14, ge=2, le=250)
    fast: int = Field(default=12, ge=2, le=120)
    slow: int = Field(default=26, ge=3, le=250)
    signal: int = Field(default=9, ge=2, le=60)
    k: int = Field(default=3, ge=1, le=30)
    d: int = Field(default=3, ge=1, le=30)
    width: float = Field(default=2, ge=0.1, le=5)

    @model_validator(mode="after")
    def check_periods(self):
        if self.kind.startswith("macd_") and self.fast >= self.slow:
            raise ValueError("MACD 短周期必须小于长周期")
        return self


class RuleOperand(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    type: Literal["constant", "indicator"] = "constant"
    value: float = Field(default=0, ge=-1e12, le=1e12)
    indicator: IndicatorSpec | None = None

    @model_validator(mode="after")
    def check_indicator(self):
        if self.type == "indicator" and self.indicator is None:
            raise ValueError("指标比较必须选择右侧指标")
        return self


class RuleCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: IndicatorSpec
    operator: Literal["gt", "gte", "lt", "lte", "cross_above", "cross_below"] = "gt"
    right: RuleOperand


class RuleGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["all", "any"] = "all"
    rules: list[RuleCondition] = Field(min_length=1, max_length=6)


class CustomStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=60)
    entry: RuleGroup
    exit: RuleGroup


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    symbols: list[str] = Field(default_factory=list, max_length=2000)
    universe: UniverseId | None = None
    universe_version: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    custom_strategy: CustomStrategy | None = None
    start: date = date(2023, 1, 1)
    end: date = date(2026, 10, 16)
    strategy: Strategy = "macd"
    initial_cash: float = Field(default=1_000_000, ge=1000, le=1e12)
    commission_bps: float = Field(default=3, ge=0, le=100)
    slippage_bps: float = Field(default=5, ge=0, le=100)
    sell_tax_bps: float = Field(default=5, ge=0, le=100)
    min_amount: float = Field(default=0, ge=0, le=1e12)
    fast: int = Field(default=12, ge=2, le=120)
    slow: int = Field(default=26, ge=3, le=250)
    signal: int = Field(default=9, ge=2, le=60)
    rsi_period: int = Field(default=14, ge=2, le=120)
    rsi_buy: float = Field(default=30, ge=1, le=49)
    rsi_sell: float = Field(default=70, ge=51, le=99)
    boll_period: int = Field(default=20, ge=2, le=120)
    boll_std: float = Field(default=2, ge=0.5, le=5)
    stoch_period: int = Field(default=9, ge=2, le=120)
    stoch_k: int = Field(default=3, ge=1, le=30)
    stoch_d: int = Field(default=3, ge=1, le=30)
    stoch_buy: float = Field(default=30, ge=1, le=49)
    stoch_sell: float = Field(default=80, ge=51, le=99)
    cci_period: int = Field(default=14, ge=2, le=120)
    cci_buy: float = Field(default=-100, ge=-500, le=-1)
    cci_sell: float = Field(default=100, ge=1, le=500)
    adx_period: int = Field(default=14, ge=2, le=120)
    adx_threshold: float = Field(default=25, ge=1, le=80)
    willr_period: int = Field(default=14, ge=2, le=120)
    willr_buy: float = Field(default=-80, ge=-99, le=-51)
    willr_sell: float = Field(default=-20, ge=-49, le=-1)
    roc_period: int = Field(default=12, ge=2, le=120)
    roc_threshold: float = Field(default=0, ge=-50, le=50)
    atr_period: int = Field(default=14, ge=2, le=120)
    atr_ma_period: int = Field(default=20, ge=2, le=250)
    atr_multiplier: float = Field(default=2, ge=0.1, le=5)
    donchian_entry: int = Field(default=20, ge=2, le=250)
    donchian_exit: int = Field(default=10, ge=2, le=250)

    @model_validator(mode="after")
    def validate_relationships(self):
        if self.universe is None:
            self.universe = "custom" if "symbols" in self.model_fields_set else "hs300"
        if self.universe == "custom" and not self.symbols:
            raise ValueError("手动证券池至少需要一只证券")
        if self.universe == "custom" and len(self.symbols) > 20:
            raise ValueError("手动证券池最多支持 20 只证券；指数股票池使用完整匹配名单")
        if self.universe == "custom" and self.universe_version is not None:
            raise ValueError("手动证券池不能指定指数版本")
        if self.strategy == "custom" and self.custom_strategy is None:
            raise ValueError("自定义策略必须提供买入及卖出规则")
        if self.start >= self.end:
            raise ValueError("开始日期必须早于结束日期")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("证券代码不能重复")
        if self.strategy in ("macd", "sma", "ema") and self.fast >= self.slow:
            raise ValueError("短周期必须小于长周期")
        if self.strategy == "donchian" and self.donchian_exit > self.donchian_entry:
            raise ValueError("唐奇安退出周期不能大于突破周期")
        return self


class ComparisonRequest(BacktestRequest):
    @model_validator(mode="after")
    def validate_all_strategies(self):
        if self.fast >= self.slow:
            raise ValueError("全策略对比要求短周期小于长周期")
        if self.donchian_exit > self.donchian_entry:
            raise ValueError("全策略对比要求唐奇安退出周期不大于突破周期")
        return self


class FactorEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    config: BacktestRequest
    factors: list[IndicatorSpec] = Field(min_length=1, max_length=24)
    horizons: list[int] = Field(default_factory=lambda: [1, 5, 10, 20], min_length=1, max_length=6)
    primary_horizon: int = Field(default=5, ge=1, le=60)
    groups: int = Field(default=5, ge=2, le=10)
    min_samples: int = Field(default=10, ge=3, le=2000)
    normalization: Literal["relative_price", "raw"] = "relative_price"

    @model_validator(mode="after")
    def validate_evaluation(self):
        if any(horizon < 1 or horizon > 60 for horizon in self.horizons):
            raise ValueError("收益周期必须为 1–60 个交易日")
        if len(set(self.horizons)) != len(self.horizons) or self.primary_horizon not in self.horizons:
            raise ValueError("收益周期不能重复，且必须包含主评价周期")
        if self.min_samples < self.groups:
            raise ValueError("最少截面样本数不能小于分层数")
        from .indicators import indicator_label

        labels = [indicator_label(spec) for spec in self.factors]
        if len(set(labels)) != len(labels):
            raise ValueError("不能重复评价相同参数的因子")
        return self
