from sample import shift_left


def test_shift_left():
    assert shift_left([1, 2, 3])[:2] == [2, 3]

