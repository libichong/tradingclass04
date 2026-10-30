import json
import os

from .factor_dsl import GRAMMAR
from .harness_models import FactorProposal


def azure_status() -> dict:
    return {
        "endpoint_configured": bool(os.environ.get("AZURE_OPENAI_ENDPOINT")),
        "api_key_configured": bool(os.environ.get("AZURE_OPENAI_API_KEY")),
        "deployment": os.environ.get("AZURE_OPENAI_DEPLOYMENT", os.environ.get("AZURE_OPENAI_MODEL", "gpt-5.6-terra")),
        "authentication": "api_key" if os.environ.get("AZURE_OPENAI_API_KEY") else "entra_id",
    }


class AzureProposer:
    def __init__(self):
        from openai import AzureOpenAI

        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            raise ValueError("Azure 模式需要设置 AZURE_OPENAI_ENDPOINT；不会使用聊天中暴露的密钥")
        auth = {}
        key = os.environ.get("AZURE_OPENAI_API_KEY")
        if key:
            auth["api_key"] = key
        else:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            self.credential = DefaultAzureCredential()
            auth["azure_ad_token_provider"] = get_bearer_token_provider(
                self.credential, "https://cognitiveservices.azure.com/.default",
            )
        self.client = AzureOpenAI(
            azure_endpoint=endpoint, api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
            timeout=90, max_retries=1, **auth,
        )
        self.deployment = azure_status()["deployment"]
        self.public_history: list[dict] = []

    def propose(self, iteration: int, count: int, public_feedback: list[dict]) -> list[FactorProposal]:
        # Only generic DSL recipes and ordinal outcomes leave the machine; no market/project data or metrics.
        history = [{**recipe, "outcome": outcome["outcome"]}
                   for recipe, outcome in zip(self.public_history[-len(public_feedback):], public_feedback)] if public_feedback else []
        response = self.client.chat.completions.create(
            model=self.deployment,
            messages=[
                {"role": "system", "content": "Propose generic quantitative factor hypotheses using only this safe DSL. "
                 "Return JSON {\"proposals\":[{\"expression\":\"...\",\"hypothesis\":\"...\",\"change\":\"...\"}]}. "
                 "Do not request code execution, data, credentials, tickers or files. " + GRAMMAR},
                {"role": "user", "content": json.dumps({"round": iteration, "count": count, "generic_feedback": history})},
            ],
            response_format={"type": "json_object"}, max_completion_tokens=2500,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Azure LLM 返回了空提案")
        parsed = json.loads(content)
        proposals = parsed.get("proposals")
        if not isinstance(proposals, list) or not 1 <= len(proposals) <= count:
            raise ValueError("Azure LLM 提案数量或 JSON 结构不合法")
        result = [FactorProposal.model_validate(item) for item in proposals]
        self.public_history.extend({"expression": item.expression, "hypothesis": item.hypothesis} for item in result)
        return result

    def close(self):
        self.client.close()
        if hasattr(self, "credential"):
            self.credential.close()


class LocalProposer:
    """Explicit deterministic research recipes; never presented as LLM output."""

    def __init__(self, baseline: str):
        self.baseline = baseline
        self.incumbent = baseline

    def propose(self, iteration: int, count: int, public_feedback: list[dict]) -> list[FactorProposal]:
        kind = self.baseline.split("(")[0]
        windows = [5, 7, 10, 20, 28, 40, 60]
        recipes = [
            (f"{kind}({n})", "改变观察窗口，检验信号时间尺度") for n in windows
        ] + [
            (f"-{self.baseline}", "检验预先声明的反向信号假设"),
            (f"rank({self.baseline})", "截面秩变换抑制量纲与极端数值影响"),
            (f"mean({self.baseline},3)", "平滑信号以减少短期噪声"),
            ("-roc(5)", "检验短期反转假设"), ("-roc(10)", "检验稍长周期反转假设"),
            ("roc(20)-roc(5)", "分离中期趋势与短期冲击"),
            ("rank(roc(20))-rank(roc(5))", "中短期动量差的稳健排序"),
            ("rank(adx(14))-rank(atr(14))", "检验趋势强度与相对波动的互补"),
            ("rank(rsi(14))-rank(atr(14))", "相对强度扣除波动暴露"),
            ("-rank(roc(5))-rank(atr(14))", "低波动短期反转组合"),
            ("rank(roc(20))-rank(atr(14))", "中期动量与波动控制组合"),
            ("rank(adx(14))+rank(roc(20))", "趋势强度与方向联合"),
            ("rank(adx(14))-rank(roc(5))", "趋势强度结合短期反转"),
            ("rank(adx(28))-rank(atr(14))", "较长趋势窗口结合波动控制"),
            ("rank(volume(20))-rank(roc(5))", "量能变化与短期反转联合"),
            ("rank(sma(20))-rank(atr(14))", "价格均值偏离与波动控制"),
            ("rank(adx(7))-rank(atr(28))", "多尺度趋势与波动组合"),
        ]
        start = (iteration - 1) * count
        selected = recipes[start:start + count]
        fallback = not selected
        if not selected:
            selected = [(f"rank({self.baseline})-{weight / 10:g}*rank(atr(14))",
                         "调整波动惩罚权重，验证风险暴露假设") for weight in range(1, 10)]
            offset = ((iteration - 1) * count - len(recipes)) % len(selected)
            selected = (selected * 2)[offset:offset + count]
        result = [FactorProposal(expression=expression, hypothesis=hypothesis, change=f"预注册本地配方 #{start + i + 1}",
                                 parent="baseline" if start + i < 10 or fallback else "new")
                  for i, (expression, hypothesis) in enumerate(selected)]
        if iteration >= 2 and self.incumbent != self.baseline:
            result[-1] = FactorProposal(
                expression=f"rank({self.incumbent})-{min(iteration, 9) / 10:g}*rank(atr(14))",
                hypothesis="在当前晋升因子上增加可控波动惩罚，检验可继承的局部变异",
                change="当前冠军的秩变换与波动惩罚变异", parent="incumbent",
            )
        return result

    def close(self):
        pass
