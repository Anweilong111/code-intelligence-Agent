def call_or_raise(callback):
    try:
        return callback()
    except Exception:
        pass

