from datetime import date
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

Strategy = Literal[
    "macd", "rsi", "sma", "boll", "ema", "kdj", "cci", "adx",
    "willr", "roc", "atr", "donchian",
]
STRATEGIES = get_args(Strategy)
HLC_STRATEGIES = frozenset({"kdj", "cci", "adx", "willr", "atr", "donchian"})


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    symbols: list[str] = Field(default=["000001.SZ"], min_length=1, max_length=20)
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
