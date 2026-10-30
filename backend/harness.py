import argparse
import ast
import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, Lock
from pathlib import Path
from uuid import uuid4

from .factor_dsl import FactorComputer, parse_expression
from .harness_eval import evaluate_expression, portfolio, promotion_reason
from .harness_llm import AzureProposer, LocalProposer
from .harness_models import HarnessRequest
from .qlib_data import QlibData
from .storage import ResearchStore

logger = logging.getLogger("uvicorn.error")


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact(metrics: dict) -> dict:
    return {key: value for key, value in metrics.items() if key not in {"curve", "daily_ic"}}


def safe_error(error: Exception) -> str:
    text = f"{type(error).__name__}: {error}"
    key = os.environ.get("AZURE_OPENAI_API_KEY")
    if key:
        text = text.replace(key, "[REDACTED]")
    return text[:1000]


class RunCancelled(Exception):
    pass


class HarnessBusyError(ValueError):
    pass


class HarnessLease:
    def __init__(self, path: Path):
        self.file = path.with_suffix(".harness.lock").open("a+b")
        if self.file.seek(0, 2) == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise HarnessBusyError("已有其他进程执行挖掘实验，请等待完成后重试") from error

    def close(self):
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()


class HarnessRunner:
    def __init__(self, store: ResearchStore, provider: QlibData | None = None):
        self.store = store
        self.provider = provider

    def execute(self, identifier: str, cancelled: Event, proposer=None):
        document = self.store.load_harness(identifier)
        config = HarnessRequest.model_validate(document["request"])

        def persist(stage: str, message: str):
            document["stage"] = stage
            document["events"].append({"at": stamp(), "stage": stage, "message": message})
            self.store.save_harness(document)

        def check_cancel():
            if cancelled.is_set():
                raise RunCancelled()

        source = proposer
        try:
            document["status"] = "running"
            persist("data", "读取历史成分股与 Qlib 日频数据；测试区间尚未参与计算")
            check_cancel()
            provider = self.provider or QlibData()
            development = provider.load(config.universe, config.train_start, config.validation_end)
            check_cancel()
            document["data"] = {
                "provider": "qlib-bin", "development_fingerprint": development.fingerprint,
                "development_securities": len(development.symbols),
                "price_convention": "Qlib open/high/low/close 原值为已归一化复权价格，不再次乘 factor",
                "membership": "历史成分股区间按信号日生效，非最新静态名单",
            }
            computer = FactorComputer(development)
            source = source or (AzureProposer() if config.mode == "azure" else LocalProposer(config.baseline))

            def measure(expression: str):
                values = computer.compute(expression)
                return {
                    "train": compact(evaluate_expression(development, values, config.train_start, config.train_end, config)),
                    "validation": compact(evaluate_expression(
                        development, values, config.validation_start, config.validation_end, config,
                    )),
                }

            persist("baseline", f"固定基准 {config.baseline}、方向、调仓、费用和晋升规则")
            baseline = {"id": "e0", "iteration": 0, "expression": config.baseline,
                        "hypothesis": "固定的已有技术因子基准，不根据测试集调整方向",
                        "change": "baseline", "parent_id": None, "compared_to": None,
                        "status": "baseline", "reason": "预先固定基准", **measure(config.baseline)}
            if any(baseline[split][key] is None for split in ("train", "validation") for key in ("ic_mean", "icir", "sharpe")):
                raise ValueError("基准的 IC、ICIR 或夏普不可计算，不能开始晋升比较")
            if any(baseline[split]["valid_days"] < 60 or baseline[split]["coverage"] < config.min_coverage
                   for split in ("train", "validation")):
                raise ValueError("基准覆盖率或有效日期不足，不能以不合格基准开始搜索")
            document["experiments"].append(baseline)
            incumbent = baseline
            document["champion_id"] = baseline["id"]
            seen = {ast.dump(parse_expression(config.baseline))}
            public_feedback = []
            for iteration in range(1, config.iterations + 1):
                check_cancel()
                persist("propose", f"第 {iteration} 轮：提出假设；模式 {config.mode}，不读取测试指标")
                proposal_parent = incumbent["id"]
                proposals = source.propose(iteration, config.candidates_per_iteration, public_feedback[-12:])
                for proposal in proposals:
                    check_cancel()
                    experiment = {
                        "id": f"e{len(document['experiments'])}", "iteration": iteration,
                        **proposal.model_dump(exclude={"parent"}),
                        "parent_id": (baseline["id"] if proposal.parent == "baseline" else
                                      proposal_parent if proposal.parent == "incumbent" else None),
                        "compared_to": incumbent["id"],
                    }
                    try:
                        canonical = ast.dump(parse_expression(proposal.expression))
                        if canonical in seen:
                            raise ValueError("与已有实验重复的表达式，保留记录但不重复计分")
                        seen.add(canonical)
                        persist("evaluate", f"{experiment['id']}：编译白名单 DSL，执行训练与开发验证")
                        experiment.update(measure(proposal.expression))
                        accepted, reason = promotion_reason(experiment, incumbent, baseline["train"], config)
                        experiment.update(status="promoted" if accepted else "rejected", reason=reason)
                        if accepted:
                            incumbent = experiment
                            document["champion_id"] = experiment["id"]
                            if isinstance(source, LocalProposer):
                                source.incumbent = experiment["expression"]
                    except (ValueError, FloatingPointError) as error:
                        experiment.update(status="invalid", reason=safe_error(error))
                        logger.warning("Harness candidate %s invalid: %s", experiment["id"], experiment["reason"])
                    document["experiments"].append(experiment)
                    public_feedback.append({"outcome": experiment["status"]})
                    persist("feedback", f"{experiment['id']}：{experiment['reason']}")
                document["rounds"].append({
                    "iteration": iteration, "champion_id": incumbent["id"], "expression": incumbent["expression"],
                    "ic": incumbent["validation"]["ic_mean"], "icir": incumbent["validation"]["icir"],
                    "rank_ic": incumbent["validation"]["rank_ic_mean"], "sharpe": incumbent["validation"]["sharpe"],
                })
                self.store.save_harness(document)
            check_cancel()
            document["frozen_champion"] = {
                "id": incumbent["id"], "expression": incumbent["expression"], "frozen_at": stamp(),
                "selection_basis": "开发验证 IC/ICIR 双提升；测试结果不参与选择",
            }
            persist("freeze", "冻结最终表达式；此后不再提出候选，不允许依据测试结果换冠军")
            check_cancel()
            test_panel = provider.load(config.universe, config.train_start, config.test_end)
            test_computer = FactorComputer(test_panel)
            document["data"]["test_fingerprint"] = test_panel.fingerprint
            document["data"]["test_securities_with_history"] = len(test_panel.symbols)
            document["holdout"] = {}
            for label, item in (("baseline", baseline), ("champion", incumbent)):
                check_cancel()
                metrics = evaluate_expression(test_panel, test_computer.compute(item["expression"]),
                                              config.test_start, config.test_end, config)
                document["holdout"][label] = {"id": item["id"], "expression": item["expression"], **metrics}
            values = test_computer.compute(config.baseline)
            check_cancel()
            document["holdout"]["benchmark"] = portfolio(
                test_panel, values, config.test_start, config.test_end, config, benchmark=True,
            )
            check_cancel()
            a, b = document["holdout"]["baseline"], document["holdout"]["champion"]
            document["conclusion"] = {
                "validation_improved": incumbent["id"] != baseline["id"],
                "test_ic_and_icir_improved": (
                    a["ic_mean"] is not None and b["ic_mean"] is not None and
                    a["icir"] is not None and b["icir"] is not None and
                    b["ic_mean"] > a["ic_mean"] and b["icir"] > a["icir"]
                ),
                "message": "测试只作冻结后审计。改善不保证样本外成立；没有晋升时如实保留基准。"
            }
            document["status"] = "completed"
            persist("completed", "实验结束：保留全部候选、拒绝原因、冠军轨迹与封存测试结果")
        except RunCancelled:
            document["status"] = "cancelled"
            persist("cancelled", "用户或服务关闭请求取消；不输出未完成的成功结论")
        except Exception as error:
            document["status"] = "failed"
            document["error"] = safe_error(error)
            logger.error("Harness %s failed: %s", identifier, document["error"])
            persist("failed", document["error"])
        finally:
            if source is not None:
                source.close()


def new_run(config: HarnessRequest) -> dict:
    sources = ("harness.py", "harness_models.py", "harness_eval.py", "harness_llm.py",
               "factor_dsl.py", "qlib_data.py", "evaluation.py", "indicators.py")
    digest = hashlib.sha256()
    for name in sources:
        digest.update(name.encode())
        digest.update(Path(__file__).with_name(name).read_bytes())
    return {
        "id": str(uuid4()), "created_at": stamp(), "request": config.model_dump(mode="json"),
        "protocol_version": "factor-rd-v1", "implementation_hash": digest.hexdigest(),
        "status": "queued", "stage": "queued", "experiments": [], "events": [], "rounds": [],
        "champion_id": None, "holdout": None, "error": None,
        "warnings": [
            "local 是确定性配方搜索，不是 LLM；azure 才会调用真实部署，失败不会悄悄切回本地。",
            "只保证获准晋升的开发验证 IC/ICIR 轨迹单调改善；不保证候选或样本外指标改善。",
            "开发验证集被重复使用，属于搜索集而非独立测试；跨任务反复查看同一测试集也会过拟合。",
            "IC 标签逐日重叠，ICIR 不年化，不作独立样本显著性认证。",
            "IC 使用未来开盘完整标签；组合选择仅用前一日信号，缺失开盘不替补，持仓缺价沿用最近估值。",
            "历史成分股区间仅按文件记录使用，其来源及生效日的真实性仍需数据提供方审计。",
            "碎份额、零无风险利率、固定费用；不模拟整手、涨跌停、借券、市场冲击、退市清算和市值行业中性化。",
            "基准为同历史池等权轮动、含相同费用，并非官方指数。期末按收盘估值，不强制清仓。",
            "收益率与IC优化目标不同；夏普最多允许下降0.1，最大回撤最多恶化5个百分点，并非每项绩效都变好。",
            "仅通用算子说明、轮数和晋升/拒绝标签可发送给 Azure；不发送行情、证券代码、收益数值、项目代码或凭据到提示词。",
        ],
    }


class HarnessManager:
    def __init__(self, store: ResearchStore):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="factor-harness")
        self.lock = Lock()
        self.cancellations: dict[str, Event] = {}
        try:
            lease = HarnessLease(store.path)
        except HarnessBusyError:
            logger.info("Another process owns the harness lease; leave its checkpoints intact")
        else:
            try:
                for item in store.list_harness():
                    if item["status"] in {"queued", "running"}:
                        document = store.load_harness(item["id"])
                        document.update(status="interrupted", stage="interrupted", error="服务重启导致任务中断；保留检查点，请显式创建新实验")
                        store.save_harness(document)
            finally:
                lease.close()

    def start(self, config: HarnessRequest) -> dict:
        with self.lock:
            if any(not event.is_set() for event in self.cancellations.values()):
                raise ValueError("已有挖掘任务在运行，请等待或取消后再创建")
            lease = HarnessLease(self.store.path)
            try:
                document = new_run(config)
                self.store.save_harness(document)
            except Exception:
                lease.close()
                raise
            event = Event()
            self.cancellations[document["id"]] = event

            def work():
                try:
                    HarnessRunner(self.store).execute(document["id"], event)
                finally:
                    event.set()
                    lease.close()

            self.executor.submit(work)
            return document

    def cancel(self, identifier: str) -> bool:
        event = self.cancellations.get(identifier)
        if event is None or event.is_set():
            return False
        event.set()
        return True

    def close(self):
        for event in self.cancellations.values():
            event.set()
        self.executor.shutdown(wait=True, cancel_futures=False)


def main():
    parser = argparse.ArgumentParser(description="Run an auditable factor R&D harness")
    parser.add_argument("--config", help="HarnessRequest JSON file; credentials must be environment variables")
    parser.add_argument("--iterations", type=int, default=8)
    args = parser.parse_args()
    if args.config:
        with open(args.config, encoding="utf-8") as file:
            config = HarnessRequest.model_validate(json.load(file))
    else:
        config = HarnessRequest(iterations=args.iterations)
    store = ResearchStore()
    lease = HarnessLease(store.path)
    try:
        document = new_run(config)
        store.save_harness(document)
        print(f"run_id={document['id']}", flush=True)
        HarnessRunner(store).execute(document["id"], Event())
    finally:
        lease.close()
    result = store.load_harness(document["id"])
    print(json.dumps({"id": result["id"], "status": result["status"],
                      "champion": result.get("frozen_champion"), "conclusion": result.get("conclusion"),
                      "error": result.get("error")}, ensure_ascii=True), flush=True)
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
