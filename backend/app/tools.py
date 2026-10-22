"""Small, deterministic tools; expressions never reach Python eval()."""
import ast
import operator
import re


OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def calculate(expression: str) -> float:
    if len(expression) > 80:
        raise ValueError("Expression is too long")
    tree = ast.parse(expression, mode="eval")

    def walk(node, depth=0):
        if depth > 12:
            raise ValueError("Expression is too deep")
        if isinstance(node, ast.Expression):
            return walk(node.body, depth + 1)
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            if abs(node.value) > 1_000_000_000:
                raise ValueError("Number is too large")
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = walk(node.operand, depth + 1)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            left, right = walk(node.left, depth + 1), walk(node.right, depth + 1)
            result = OPS[type(node.op)](left, right)
            if abs(result) > 1_000_000_000:
                raise ValueError("Result is too large")
            return result
        raise ValueError("Unsupported expression")

    return float(walk(tree))


def extract_expression(text: str) -> str | None:
    match = re.search(r"(?:计算|calculate)\s*[:：]?\s*([0-9+*/().\-\s]{3,80})", text, flags=re.I)
    return match.group(1).strip() if match else None
