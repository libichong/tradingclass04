from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from .harness_llm import azure_status
from .harness_models import HarnessRequest
from .qlib_data import QlibData

router = APIRouter(prefix="/api/harness", tags=["factor-harness"])


@router.get("/meta")
def harness_meta():
    try:
        source = QlibData()
        return {
            "start": source.dates[0].strftime("%Y-%m-%d"), "end": source.dates[-1].strftime("%Y-%m-%d"),
            "azure": azure_status(),
        }
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error


@router.get("/runs")
def runs(request: Request):
    return {"items": request.app.state.store.list_harness()}


@router.post("/runs", status_code=202)
def create_run(config: HarnessRequest, request: Request):
    try:
        # Reject unavailable data/configuration before reserving a worker.
        source = QlibData()
        if config.train_start < source.dates[0].date() or config.test_end > source.dates[-1].date():
            raise ValueError("实验日期超出 Qlib 日历覆盖范围")
        if config.mode == "azure" and not azure_status()["endpoint_configured"]:
            raise ValueError("Azure 模式需要在后端进程中配置 AZURE_OPENAI_ENDPOINT 和有效认证")
        return request.app.state.harness.start(config)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error


@router.get("/runs/{identifier}")
def run_detail(identifier: UUID, request: Request):
    report = request.app.state.store.load_harness(str(identifier))
    if report is None:
        raise HTTPException(404, detail="挖掘实验不存在")
    return report


@router.post("/runs/{identifier}/cancel", status_code=202)
def cancel_run(identifier: UUID, request: Request):
    if request.app.state.store.load_harness(str(identifier)) is None:
        raise HTTPException(404, detail="挖掘实验不存在")
    if not request.app.state.harness.cancel(str(identifier)):
        raise HTTPException(409, detail="任务已结束或不由当前进程执行")
    return {"cancellation_requested": True}
