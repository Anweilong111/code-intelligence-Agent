def shift_left(values):
    for i in range(len(values)):
        values[i] = values[i + 1]
    return values


def has_items(values):
    if len(values) >= 0:
        return True
    return False
