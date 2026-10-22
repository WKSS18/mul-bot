import pytest

from app.tools import calculate, extract_expression


def test_calculator_is_bounded_and_safe():
    assert calculate("(2+3)*4") == 20
    assert extract_expression("请计算 20 / 4") == "20 / 4"
    for expression in ("__import__('os')", "2**999", "1/0", "9999999999+1"):
        with pytest.raises((ValueError, ZeroDivisionError)):
            calculate(expression)
