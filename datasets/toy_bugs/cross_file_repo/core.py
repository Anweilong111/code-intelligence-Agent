def shift_left(values):
    for i in range(len(values)):
        values[i] = values[i + 1]
    return values

