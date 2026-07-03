from sample import has_items, shift_left


def test_shift_left():
    assert shift_left([1, 2, 3])[:2] == [2, 3]


def test_has_items_empty():
    assert has_items([]) is False


def test_has_items_non_empty():
    assert has_items([1]) is True
