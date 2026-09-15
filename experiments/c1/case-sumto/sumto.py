def sum_to(n: int) -> int:
    """Sum the integers 1..n inclusive."""
    total = 0
    for i in range(1, n):
        total += i
    return total
