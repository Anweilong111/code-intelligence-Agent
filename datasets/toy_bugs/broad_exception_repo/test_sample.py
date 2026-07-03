import pytest

from sample import call_or_raise


def raise_value_error():
    raise ValueError("boom")


def test_callback_exception_is_not_swallowed():
    with pytest.raises(ValueError):
        call_or_raise(raise_value_error)


def test_callback_success_value():
    assert call_or_raise(lambda: 7) == 7

