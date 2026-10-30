import logging
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .data import MarketData
from .comparison import run_comparison
from .engine import run_backtest
from .evaluation import run_factor_evaluation
from .harness import HarnessManager
from .harness_api import router as harness_router
from .models import STRATEGIES, BacktestRequest, ComparisonRequest, CustomStrategy, FactorEvaluationRequest, UniverseId
from .storage import ResearchStore
from .universes import INDEXES, UniverseError, resolve_universe, sync_universe, universe_summary

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.market = MarketData()
    app.state.store = ResearchStore()
    app.state.harness = HarnessManager(app.state.store)
    try:
        yield
    finally:
        app.state.harness.close()


app = FastAPI(title="Factor Lab API", version="1.0.0", lifespan=lifespan)
app.include_router(harness_router)


@app.exception_handler(UniverseError)
async def universe_error(request: Request, error: UniverseError):
    logger.warning("Universe source error: %s", error)
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.get("/api/universes")
def universes(request: Request):
    store: ResearchStore = request.app.state.store
    market: MarketData = request.app.state.market
    items = []
    for identifier, info in INDEXES.items():
        snapshot = store.load_universe(identifier)
        items.append(universe_summary(snapshot, market.symbols) if snapshot else {
            "id": identifier, **info, "symbols": [], "matched": 0, "missing": [],
            "version": None, "as_of": None, "fetched_at": None, "source": None,
        })
    return {"items": items}


@app.post("/api/universes/{identifier}/sync")
def sync_index(identifier: UniverseId, request: Request):
    if identifier not in INDEXES:
        raise HTTPException(422, detail="手动证券池无需同步指数名单")
    snapshot = sync_universe(request.app.state.store, identifier)
    return universe_summary(snapshot, request.app.state.market.symbols)


@app.get("/api/strategies")
def saved_strategies(request: Request):
    return {"items": request.app.state.store.list_strategies()}


@app.post("/api/strategies", status_code=201)
def create_strategy(definition: CustomStrategy, request: Request):
    return request.app.state.store.save_strategy(definition)


@app.put("/api/strategies/{identifier}")
def update_strategy(identifier: UUID, definition: CustomStrategy, request: Request):
    store: ResearchStore = request.app.state.store
    if not any(item["id"] == str(identifier) for item in store.list_strategies()):
        raise HTTPException(404, detail="自定义策略不存在")
    return store.save_strategy(definition, str(identifier))


@app.delete("/api/strategies/{identifier}")
def delete_strategy(identifier: UUID, request: Request):
    if not request.app.state.store.delete_strategy(str(identifier)):
        raise HTTPException(404, detail="自定义策略不存在")
    return {"deleted": True}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/meta")
def metadata(request: Request):
    market: MarketData = request.app.state.market
    return {
        "start": market.dates[0].strftime("%Y-%m-%d"),
        "end": market.dates[-1].strftime("%Y-%m-%d"),
        "days": len(market.dates), "securities": len(market.symbols),
        "files": market.files, "engine": "TA-Lib",
        "strategies": STRATEGIES,
    }


@app.get("/api/symbols")
def symbols(request: Request, q: str = Query("", max_length=30), limit: int = Query(30, ge=1, le=100)):
    market: MarketData = request.app.state.market
    matches = [symbol for symbol in market.symbols if q.upper() in symbol]
    return {"items": matches[:limit], "total": len(matches)}


def validate_market_config(market: MarketData, config: BacktestRequest):
    unknown = [symbol for symbol in config.symbols if symbol not in market.symbols] if config.universe == "custom" else []
    if unknown:
        raise HTTPException(422, detail=f"未知证券代码：{', '.join(unknown)}")
    if config.start < market.dates[0].date() or config.end > market.dates[-1].date():
        raise HTTPException(422, detail="回测日期超出数据覆盖范围")
    if ((market.dates.date >= config.start) & (market.dates.date <= config.end)).sum() < 2:
        raise HTTPException(422, detail="所选区间至少需要两个实际交易日")


def add_universe_notes(result: dict, universe: dict):
    result["universe"] = universe
    notes = []
    if universe["id"] != "custom":
        notes = [
            f"股票池：{universe['name']}，快照日期 {universe['as_of']}，匹配 {universe['matched']} / {len(universe['symbols'])} 只。",
            universe["warning"],
            "基准仍为同池等初始资金买入持有，不是该指数的官方收益曲线。",
        ]
        if universe["missing"]:
            notes.append(f"本地缺少 {len(universe['missing'])} 只成分股行情，不参与回测；其余匹配证券等额分配资金。")
    if "results" in result:
        for row in result["results"]:
            row["warnings"].extend(notes)
    else:
        result["warnings"].extend(notes)
    return result


@app.post("/api/backtest")
def backtest(config: BacktestRequest, request: Request):
    market: MarketData = request.app.state.market
    validate_market_config(market, config)
    try:
        resolved, universe = resolve_universe(request.app.state.store, config, market.symbols)
        return add_universe_notes(run_backtest(market.load(tuple(resolved.symbols)), resolved), universe)
    except ValueError as error:
        logger.warning("Backtest rejected: %s", error)
        raise HTTPException(422, detail=str(error)) from error


@app.post("/api/compare")
def compare(config: ComparisonRequest, request: Request):
    market: MarketData = request.app.state.market
    validate_market_config(market, config)
    try:
        resolved, universe = resolve_universe(request.app.state.store, config, market.symbols)
        return add_universe_notes(run_comparison(market.load(tuple(resolved.symbols)), resolved), universe)
    except ValueError as error:
        logger.warning("Comparison rejected: %s", error)
        raise HTTPException(422, detail=str(error)) from error


@app.post("/api/factors/evaluate")
def evaluate_factors(definition: FactorEvaluationRequest, request: Request):
    market: MarketData = request.app.state.market
    validate_market_config(market, definition.config)
    try:
        resolved, universe = resolve_universe(request.app.state.store, definition.config, market.symbols)
        evaluation = definition.model_copy(update={"config": resolved})
        report = run_factor_evaluation(market.load(tuple(resolved.symbols)), evaluation)
        report["universe"] = universe
        if universe["id"] != "custom":
            report["warnings"].append(universe["warning"])
            if universe["missing"]:
                report["warnings"].append(f"本地缺少 {len(universe['missing'])} 只成分股，已明确排除。")
        return report
    except ValueError as error:
        logger.warning("Factor evaluation rejected: %s", error)
        raise HTTPException(422, detail=str(error)) from error
