import pytest

from calc import add, divide


def test_add():
    assert add(2, 3) == 5


def test_divide():
    assert divide(10, 4) == 2.5


def test_divide_by_zero_raises_value_error():
    with pytest.raises(ValueError, match="divide by zero"):
        divide(1, 0)
