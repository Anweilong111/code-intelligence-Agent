from sample import has_items


def test_has_items_empty():
    assert has_items([]) is False


def test_has_items_non_empty():
    assert has_items([1]) is True

