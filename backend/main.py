import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request

from .data import MarketData
from .comparison import run_comparison
from .engine import run_backtest
from .models import STRATEGIES, BacktestRequest, ComparisonRequest

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.market = MarketData()
    yield


app = FastAPI(title="Factor Lab API", version="1.0.0", lifespan=lifespan)


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
    unknown = [symbol for symbol in config.symbols if symbol not in market.symbols]
    if unknown:
        raise HTTPException(422, detail=f"未知证券代码：{', '.join(unknown)}")
    if config.start < market.dates[0].date() or config.end > market.dates[-1].date():
        raise HTTPException(422, detail="回测日期超出数据覆盖范围")


@app.post("/api/backtest")
def backtest(config: BacktestRequest, request: Request):
    market: MarketData = request.app.state.market
    validate_market_config(market, config)
    try:
        return run_backtest(market.load(tuple(config.symbols)), config)
    except ValueError as error:
        logger.warning("Backtest rejected: %s", error)
        raise HTTPException(422, detail=str(error)) from error


@app.post("/api/compare")
def compare(config: ComparisonRequest, request: Request):
    market: MarketData = request.app.state.market
    validate_market_config(market, config)
    try:
        return run_comparison(market.load(tuple(config.symbols)), config)
    except ValueError as error:
        logger.warning("Comparison rejected: %s", error)
        raise HTTPException(422, detail=str(error)) from error
