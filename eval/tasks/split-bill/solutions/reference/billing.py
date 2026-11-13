"""Splitting a bill."""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def allocate(total_cents, weights):
    if not _is_int(total_cents) or not all(_is_int(w) for w in weights):
        raise TypeError("total_cents and weights must be ints")
    if not weights or any(w < 0 for w in weights) or sum(weights) == 0:
        raise ValueError("weights must be non-negative and not all zero")
    sign = -1 if total_cents < 0 else 1
    total, weight_sum = abs(total_cents), sum(weights)
    shares = [total * w // weight_sum for w in weights]
    remainders = [total * w % weight_sum for w in weights]
    leftover = total - sum(shares)
    order = sorted(range(len(weights)), key=lambda i: (-remainders[i], i))
    for index in order[:leftover]:
        shares[index] += 1
    return [sign * share for share in shares]
