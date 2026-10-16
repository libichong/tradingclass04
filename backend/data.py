from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.feather as feather
import pyarrow.ipc as ipc

FIELDS = ("open", "high", "low", "close", "amt", "num_trades", "cumadj")
DATA_DIR = Path(__file__).resolve().parents[1] / "feathers"


class DataError(RuntimeError):
    pass


class MarketData:
    def __init__(self, directory: Path = DATA_DIR):
        self.directory = directory
        self.dates = pd.DatetimeIndex([])
        self.symbols: list[str] = []
        self.files: list[dict] = []
        for field in FIELDS:
            path = directory / f"{field}.feather"
            if not path.is_file():
                raise DataError(f"缺少数据文件：{path.name}")
            with pa.memory_map(str(path), "r") as source:
                names = ipc.open_file(source).schema.names
            if not names or names[0] != "date":
                raise DataError(f"{path.name} 必须包含首列 date")
            dates = pd.DatetimeIndex(
                feather.read_table(path, columns=["date"]).column("date").to_pandas()
            )
            if dates.hasnans or not dates.is_unique or not dates.is_monotonic_increasing:
                raise DataError(f"{path.name} 日期存在空值、重复或非升序")
            if field == FIELDS[0]:
                self.dates, self.symbols = dates, names[1:]
                if len(dates) < 2 or not self.symbols:
                    raise DataError("数据至少需要两个交易日和一只证券")
            elif not dates.equals(self.dates) or names[1:] != self.symbols:
                raise DataError(f"{path.name} 的日期或证券列与 open 不一致")
            last = feather.read_table(path, columns=self.symbols).slice(len(dates) - 1, 1)
            valid = sum(
                np.isfinite(last.column(i).to_numpy(zero_copy_only=False)).sum()
                for i in range(len(self.symbols))
            )
            self.files.append({
                "name": field,
                "size_mb": round(path.stat().st_size / 1024**2, 1),
                "last_day_coverage": float(valid / len(self.symbols)),
            })

    @lru_cache(maxsize=8)
    def load(self, symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
        if not symbols or any(s not in self.symbols for s in symbols):
            raise ValueError("存在未知证券代码")
        result = {}
        for field in FIELDS:
            table = feather.read_table(
                self.directory / f"{field}.feather", columns=["date", *symbols]
            )
            result[field] = table.to_pandas().set_index("date")
        return result
