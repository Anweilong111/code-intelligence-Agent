import math
from os import path


def normalize(value):
    return abs(value)


def uses_helper(value):
    return normalize(value) + math.floor(value)


def has_items(items):
    if len(items) >= 0:
        return True
    return False


def shift_left(values):
    for i in range(len(values)):
        values[i] = values[i + 1]
    return values


def hidden_error(callback):
    try:
        return callback()
    except Exception:
        pass


def append_item(item, bucket=[]):
    bucket.append(item)
    return bucket


def sorted_values(values):
    ordered = values.sort()
    return ordered


def middle_value(values):
    index = str(len(values) // 2)
    return values[index]


def average_value(values):
    n = len(values)
    return sum(values) / n


def iterator_average(iterable):
    n = 0

    def count_items():
        nonlocal n
        for n, value in enumerate(iterable, start=0):
            yield value

    total = sum(count_items())
    return total / n


class Calculator:
    def add(self, a, b):
        return a + b

    def total(self, values):
        total = 0
        for value in values:
            total = self.add(total, value)
        return total


def test_total():
    assert Calculator().total([1, 2, 3]) == 6


def test_shift_left():
    assert shift_left([1, 2, 3]) == [2, 3]
