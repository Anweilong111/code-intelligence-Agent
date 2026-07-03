from api import normalize_window


def test_normalize_window():
    assert normalize_window([1, 2, 3])[:2] == [2, 3]

