from fizzbuzz import classify

def test_fifteen_is_fizzbuzz():
    assert classify(15) == "FizzBuzz"

def test_three_is_fizz():
    assert classify(3) == "Fizz"

def test_five_is_buzz():
    assert classify(5) == "Buzz"

def test_seven_is_plain():
    assert classify(7) == "7"
