import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

QLIB_DIR = Path(os.environ.get("QLIB_DATA_DIR", Path(__file__).resolve().parents[1] / "qlib_bin"))
FIELDS = ("open", "high", "low", "close", "volume")


@dataclass
class QlibPanel:
    dates: pd.DatetimeIndex
    symbols: list[str]
    fields: dict[str, np.ndarray]
    membership: np.ndarray
    fingerprint: str


class QlibData:
    """Read Qlib's little-endian float32 day bins without executing Qlib expressions."""

    def __init__(self, directory: Path = QLIB_DIR):
        self.directory = directory
        path = directory / "calendars" / "day.txt"
        if not path.is_file():
            raise ValueError("缺少 Qlib calendars\\day.txt；请设置 QLIB_DATA_DIR")
        self.dates = pd.DatetimeIndex(pd.read_csv(path, header=None)[0])
        if self.dates.hasnans or not self.dates.is_unique or not self.dates.is_monotonic_increasing:
            raise ValueError("Qlib 日历必须唯一、升序且无缺失")

    def load(self, universe: str, start, end) -> QlibPanel:
        if universe not in ("csi300", "csi500", "csi1000"):
            raise ValueError("不支持的 Qlib 股票池")
        if pd.Timestamp(start) < self.dates[0] or pd.Timestamp(end) > self.dates[-1]:
            raise ValueError("实验日期超出 Qlib 日历覆盖范围")
        instruments = self.directory / "instruments" / f"{universe}.txt"
        if not instruments.is_file():
            raise ValueError(f"缺少历史成分股文件 {universe}.txt")
        members = pd.read_csv(instruments, sep=r"\s+", header=None, names=["symbol", "start", "end"])
        members["start"], members["end"] = pd.to_datetime(members["start"]), pd.to_datetime(members["end"])
        if (members["start"] > members["end"]).any():
            raise ValueError("Qlib 成分股日期区间不合法")
        if not members["symbol"].str.fullmatch(r"(SH|SZ|BJ)[A-Z0-9]{6}").all():
            raise ValueError("Qlib 证券代码格式不合法")
        members = members[(members["end"] >= pd.Timestamp(start)) & (members["start"] <= pd.Timestamp(end))]
        symbols = sorted(members["symbol"].unique().tolist())
        if not symbols:
            raise ValueError("该区间没有历史成分股")
        begin = max(0, self.dates.searchsorted(pd.Timestamp(start)) - 300)
        stop = self.dates.searchsorted(pd.Timestamp(end), side="right")
        dates = self.dates[begin:stop]
        fields = {field: np.full((len(dates), len(symbols)), np.nan) for field in FIELDS}
        membership = np.zeros((len(dates), len(symbols)), dtype=bool)
        digest = hashlib.sha256()
        digest.update((self.directory / "calendars" / "day.txt").read_bytes())
        digest.update(instruments.read_bytes())
        for column, symbol in enumerate(symbols):
            for row in members[members["symbol"] == symbol].itertuples():
                membership[:, column] |= (dates >= row.start) & (dates <= row.end)
            for field in FIELDS:
                path = self.directory / "features" / symbol.lower() / f"{field}.day.bin"
                if not path.is_file():
                    raise ValueError(f"缺少 Qlib 字段：{symbol} / {field}")
                content = path.read_bytes()
                digest.update(f"{symbol}:{field}:".encode())
                digest.update(content)
                if len(content) < 8 or len(content) % 4:
                    raise ValueError(f"Qlib bin 长度不合法：{symbol} / {field}")
                values = np.frombuffer(content, dtype="<f4")
                offset = float(values[0])
                if not np.isfinite(offset) or offset < 0 or offset != int(offset):
                    raise ValueError(f"Qlib bin 起始索引不合法：{symbol} / {field}")
                offset = int(offset)
                final = offset + len(values) - 1
                if final > len(self.dates):
                    raise ValueError(f"Qlib bin 超出日历：{symbol} / {field}")
                left, right = max(begin, offset), min(stop, final)
                if right > left:
                    fields[field][left - begin:right - begin, column] = values[1 + left - offset:1 + right - offset]
        if not np.isfinite(fields["open"][membership]).any():
            raise ValueError("历史成分股中没有有效开盘行情")
        return QlibPanel(dates, symbols, fields, membership, digest.hexdigest())
