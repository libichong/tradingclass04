from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HarnessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    mode: Literal["local", "azure"] = "local"
    universe: Literal["csi300", "csi500", "csi1000"] = "csi300"
    baseline: Literal["rsi(14)", "roc(12)", "adx(14)"] = "rsi(14)"
    train_start: date = date(2020, 1, 1)
    train_end: date = date(2022, 12, 31)
    validation_start: date = date(2023, 1, 1)
    validation_end: date = date(2024, 12, 31)
    test_start: date = date(2025, 1, 1)
    test_end: date = date(2026, 10, 30)
    iterations: int = Field(default=8, ge=1, le=20)
    candidates_per_iteration: int = Field(default=3, ge=1, le=5)
    horizon: int = Field(default=5, ge=1, le=20)
    top_fraction: float = Field(default=.2, ge=.05, le=.5)
    min_samples: int = Field(default=30, ge=10, le=500)
    min_coverage: float = Field(default=.9, ge=.5, le=1)
    commission_bps: float = Field(default=3, ge=0, le=100)
    slippage_bps: float = Field(default=5, ge=0, le=100)
    sell_tax_bps: float = Field(default=5, ge=0, le=100)
    min_ic_gain: float = Field(default=.0001, ge=0, le=.1)
    min_icir_gain: float = Field(default=.001, ge=0, le=1)

    @model_validator(mode="after")
    def check_dates(self):
        if not (self.train_start < self.train_end < self.validation_start < self.validation_end
                < self.test_start < self.test_end):
            raise ValueError("训练、开发验证、封存测试区间必须严格按时间排列且不重叠")
        return self


class FactorProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expression: str = Field(min_length=1, max_length=500)
    hypothesis: str = Field(min_length=1, max_length=600)
    change: str = Field(min_length=1, max_length=400)
    parent: Literal["baseline", "incumbent", "new"] = "new"
