from sample import append_item


def test_append_item_default_bucket_is_not_shared():
    assert append_item("a") == ["a"]
    assert append_item("b") == ["b"]


def test_append_item_explicit_bucket():
    bucket = ["seed"]
    assert append_item("x", bucket) == ["seed", "x"]

