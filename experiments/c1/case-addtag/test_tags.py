from tags import add_tag

def test_first_call_fresh():
    assert add_tag("a") == ["a"]

def test_second_call_fresh():
    assert add_tag("b") == ["b"]
