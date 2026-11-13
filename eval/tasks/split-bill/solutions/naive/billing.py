"""Splitting a bill."""


def allocate(total_cents, weights):
    if not weights or sum(weights) == 0:
        raise ValueError("weights must not be empty or all zero")
    weight_sum = sum(weights)
    shares = [round(total_cents * w / weight_sum) for w in weights]
    shares[-1] += total_cents - sum(shares)
    return shares
