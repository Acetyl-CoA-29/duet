"""Share a whole number of cents between parties in proportion to their weights."""
from typing import Dict, List, Mapping


def check_total(total_cents: int) -> None:
    if isinstance(total_cents, bool) or not isinstance(total_cents, int):
        raise ValueError("the total must be a whole number of cents")
    if total_cents < 0:
        raise ValueError("the total cannot be negative")


def check_weights(weights: Mapping[str, int]) -> None:
    for name, weight in weights.items():
        if isinstance(weight, bool) or not isinstance(weight, int):
            raise ValueError(f"the weight for {name} must be a whole number")
        if weight < 0:
            raise ValueError(f"the weight for {name} cannot be negative")


def allocate(total_cents: int, weights: Mapping[str, int]) -> Dict[str, int]:
    """Split `total_cents` by weight. The shares add up to `total_cents` exactly: the cents left by
    rounding down go one each to the largest remainders, ties going to the earlier name."""
    check_total(total_cents)
    check_weights(weights)
    weight_sum = sum(weights.values())
    if weight_sum == 0:
        return {}
    floors = {name: total_cents * weight // weight_sum for name, weight in weights.items()}
    remainders = {name: total_cents * weight % weight_sum for name, weight in weights.items()}
    leftover = total_cents - sum(floors.values())
    ranked = sorted(weights, key=lambda name: (-remainders[name], name))
    lucky = frozenset(ranked[:leftover])
    return {name: floors[name] + (1 if name in lucky else 0) for name in weights}


def format_cents(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def describe(shares: Mapping[str, int]) -> List[str]:
    """One line per party, largest share first, for a receipt."""
    ordered = sorted(shares.items(), key=lambda item: (-item[1], item[0]))
    return [f"{name}: {format_cents(cents)}" for name, cents in ordered]
