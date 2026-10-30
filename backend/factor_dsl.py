import ast

import numpy as np
import pandas as pd

from .indicators import calculate_indicator
from .models import IndicatorSpec
from .qlib_data import QlibPanel

FEATURES = {"rsi", "roc", "adx", "atr", "sma", "volume"}
OPERATORS = {"rank", "mean", "std", "delay", "clip"}
GRAMMAR = (
    "Expression DSL: + - * / unary -; rsi(n), roc(n), adx(n), atr(n), sma(n), volume(n); "
    "rank(x), mean(x,n), std(x,n), delay(x,n), clip(x,low,high). "
    "n integer 2..120 (delay 1..120). Constants abs<=10. "
    "rsi=RSI/100; roc=ROC/100; atr=ATR/close; sma=SMA/close-1; "
    "volume=volume/rolling_mean(volume,n)-1; rank=cross-sectional percentile rank-0.5. "
    "No lead/negative delay, Python code, identifiers, indexing, I/O or imports. Max 80 AST nodes."
)


def parse_expression(expression: str) -> ast.Expression:
    if len(expression) > 500:
        raise ValueError("因子表达式超过长度限制")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ValueError("因子表达式语法错误") from error
    if len(list(ast.walk(tree))) > 80:
        raise ValueError("因子表达式过于复杂")

    def check(node: ast.AST, depth: int = 0):
        if depth > 12:
            raise ValueError("因子嵌套过深")
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float) or not np.isfinite(node.value) or abs(node.value) > 10:
                raise ValueError("常数必须为绝对值不超过 10 的有限数")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            check(node.left, depth + 1)
            check(node.right, depth + 1)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            check(node.operand, depth + 1)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name, args = node.func.id, node.args
            if name in FEATURES and len(args) == 1:
                window(args[0])
            elif name == "rank" and len(args) == 1:
                check(args[0], depth + 1)
            elif name in {"mean", "std", "delay"} and len(args) == 2:
                check(args[0], depth + 1)
                window(args[1], minimum=1 if name == "delay" else 2)
            elif name == "clip" and len(args) == 3:
                for arg in args:
                    check(arg, depth + 1)
                if literal(args[1]) >= literal(args[2]):
                    raise ValueError("clip 边界必须是递增常数")
            else:
                raise ValueError("因子算子或参数数量不合法")
        else:
            raise ValueError("只允许白名单因子 DSL，禁止执行 Python")

    check(tree.body)
    return tree


def window(node: ast.AST, minimum: int = 2) -> int:
    if not isinstance(node, ast.Constant) or type(node.value) is not int or not minimum <= node.value <= 120:
        raise ValueError(f"窗口必须是 {minimum}–120 的正整数，禁止未来位移")
    return node.value


def literal(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return -literal(node.operand)
    raise ValueError("clip 边界必须是数值常数")


class FactorComputer:
    def __init__(self, panel: QlibPanel):
        self.panel = panel
        self.cache: dict[str, np.ndarray] = {}

    def compute(self, expression: str) -> np.ndarray:
        tree = parse_expression(expression)
        shape = self.panel.membership.shape

        def run(node: ast.AST):
            key = ast.dump(node)
            if key in self.cache:
                return self.cache[key]
            if isinstance(node, ast.Constant):
                result = np.full(shape, float(node.value))
            elif isinstance(node, ast.UnaryOp):
                result = -run(node.operand)
            elif isinstance(node, ast.BinOp):
                a, b = run(node.left), run(node.right)
                with np.errstate(over="raise", invalid="ignore"):
                    if isinstance(node.op, ast.Div):
                        result = np.divide(a, b, out=np.full(shape, np.nan), where=np.abs(b) > 1e-12)
                    else:
                        fn = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply}[type(node.op)]
                        result = fn(a, b)
            else:
                name, args = node.func.id, node.args
                if name in FEATURES:
                    period = window(args[0])
                    close, high, low = (self.panel.fields[field] for field in ("close", "high", "low"))
                    if name == "volume":
                        values = self.panel.fields["volume"]
                        average = pd.DataFrame(values).rolling(period, min_periods=period).mean().to_numpy()
                        result = np.divide(values, average, out=np.full(shape, np.nan), where=average > 0) - 1
                    else:
                        spec = IndicatorSpec(kind=name, period=period)
                        result = np.column_stack([calculate_indicator(spec, close[:, i], high[:, i], low[:, i])
                                                  for i in range(shape[1])])
                        if name in {"rsi", "roc"}:
                            result /= 100
                        elif name in {"atr", "sma"}:
                            result = np.divide(result, close, out=np.full(shape, np.nan), where=close > 0)
                            if name == "sma":
                                result -= 1
                else:
                    values = run(args[0])
                    frame = pd.DataFrame(values)
                    if name == "rank":
                        result = frame.where(self.panel.membership).rank(axis=1, pct=True, method="average").to_numpy() - .5
                    elif name == "clip":
                        result = np.clip(values, literal(args[1]), literal(args[2]))
                    elif name == "delay":
                        result = frame.shift(window(args[1], 1)).to_numpy()
                    else:
                        rolling = frame.rolling(window(args[1]), min_periods=window(args[1]))
                        result = (rolling.mean() if name == "mean" else rolling.std(ddof=1)).to_numpy()
            if isinstance(node, ast.Call) and node.func.id in FEATURES:
                self.cache[key] = result
            return result

        return np.where(self.panel.membership, run(tree.body), np.nan)
