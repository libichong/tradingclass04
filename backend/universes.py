import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from urllib.error import URLError
from urllib.request import urlopen

import pandas as pd
import xlrd

from .models import BacktestRequest
from .storage import ResearchStore

INDEXES = {
    "hs300": {"name": "沪深300", "code": "000300", "size": 300},
    "zz500": {"name": "中证500", "code": "000905", "size": 500},
    "zz1000": {"name": "中证1000", "code": "000852", "size": 1000},
    "sz50": {"name": "上证50", "code": "000016", "size": 50},
}
SNAPSHOT_WARNING = "使用最新成分股静态快照，而非历史时点成分股；历史回测存在幸存者偏差及成分股前视偏差。"


class UniverseError(RuntimeError):
    pass


def parse_constituents(content: bytes, identifier: str) -> dict:
    info = INDEXES[identifier]
    try:
        frame = pd.read_excel(BytesIO(content), engine="xlrd", dtype=str)
    except (xlrd.XLRDError, ValueError) as error:
        raise UniverseError(f"指数名单不是有效的官方 Excel：{error}") from error
    required = ["日期Date", "指数代码 Index Code", "成份券代码Constituent Code", "交易所Exchange"]
    if any(column not in frame for column in required):
        raise UniverseError("官方成分股文件字段发生变化，请检查数据源")
    if len(frame) != info["size"] or not frame["指数代码 Index Code"].eq(info["code"]).all():
        raise UniverseError("官方文件的指数代码或成分数量与预期不符")
    dates = pd.to_datetime(frame["日期Date"], format="%Y%m%d", errors="coerce")
    if dates.isna().any() or dates.nunique() != 1:
        raise UniverseError("成分股快照日期不一致或缺失")
    symbols = []
    exchanges = {"深圳证券交易所": "SZ", "上海证券交易所": "SH"}
    for code, exchange in zip(frame["成份券代码Constituent Code"], frame["交易所Exchange"]):
        if exchange not in exchanges or not isinstance(code, str) or not code.isascii() or not code.isdigit() or len(code) != 6:
            raise UniverseError("官方文件包含未支持的证券标识")
        symbols.append(f"{code}.{exchanges[exchange]}")
    if len(set(symbols)) != len(symbols):
        raise UniverseError("指数成分股存在重复")
    return {"symbols": sorted(symbols), "as_of": dates.iloc[0].strftime("%Y-%m-%d")}


def sync_universe(store: ResearchStore, identifier: str) -> dict:
    if identifier not in INDEXES:
        raise ValueError("未知指数股票池")
    info = INDEXES[identifier]
    source = f"https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{info['code']}cons.xls"
    try:
        with urlopen(source, timeout=30) as response:
            content = response.read(2_000_001)
    except (URLError, TimeoutError, OSError) as error:
        raise UniverseError(f"无法获取{info['name']}官方成分股：{error}；已有缓存不会被覆盖") from error
    if len(content) > 2_000_000:
        raise UniverseError("成分股文件超过大小限制")
    parsed = parse_constituents(content, identifier)
    version = hashlib.sha256(json.dumps(parsed, sort_keys=True).encode()).hexdigest()
    snapshot = {
        "id": identifier, "name": info["name"], "code": info["code"], **parsed,
        "version": version, "source": source, "fetched_at": datetime.now(timezone.utc).isoformat(),
        "warning": SNAPSHOT_WARNING,
    }
    store.save_universe(snapshot)
    return snapshot


def universe_summary(snapshot: dict, available: list[str]) -> dict:
    known = set(available)
    matched = [symbol for symbol in snapshot["symbols"] if symbol in known]
    return {**snapshot, "matched": len(matched), "missing": [symbol for symbol in snapshot["symbols"] if symbol not in known]}


def resolve_universe(store: ResearchStore, config: BacktestRequest, available: list[str]) -> tuple[BacktestRequest, dict]:
    if config.universe == "custom":
        unknown = set(config.symbols) - set(available)
        if unknown:
            raise ValueError(f"未知证券代码：{', '.join(sorted(unknown))}")
        return config, {"id": "custom", "name": "手动证券池", "matched": len(config.symbols), "symbols": config.symbols}
    identifier = config.universe or "hs300"
    snapshot = store.load_universe(identifier, config.universe_version)
    if snapshot is None:
        if config.universe_version:
            raise ValueError("指定的指数快照版本不存在；不能悄悄改用最新名单")
        snapshot = sync_universe(store, identifier)
    summary = universe_summary(snapshot, available)
    known = set(available)
    symbols = [symbol for symbol in snapshot["symbols"] if symbol in known]
    if not symbols:
        raise ValueError("指数名单与本地行情没有匹配证券")
    resolved = type(config).model_validate({
        **config.model_dump(), "symbols": symbols, "universe_version": snapshot["version"],
    })
    return resolved, summary
