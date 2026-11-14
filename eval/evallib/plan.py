"""The order runs happen in. Arms are shuffled afresh for every (task, seed) from a recorded seed,
so no arm is always the one that runs last."""
from __future__ import annotations

import random
from typing import List, NamedTuple, Sequence

from .tasks import Task

MAX_ORDER_SEED = 2 ** 32


class PlannedRun(NamedTuple):
    task: Task
    arm: str
    seed: int
    position: int    # 1-based place of this arm among the arms of its (task, seed)
    of: int


def new_order_seed() -> int:
    return random.SystemRandom().randrange(MAX_ORDER_SEED)


def arm_order(arm_names: Sequence[str], task_name: str, seed: int, order_seed: int) -> List[str]:
    """The arms in the order they run for one (task, seed); the same inputs always give the same order."""
    shuffler = random.Random(f"{order_seed}:{task_name}:{seed}")
    return shuffler.sample(list(arm_names), len(arm_names))


def make_plan(tasks: Sequence[Task], arm_names: Sequence[str], seeds: int, order_seed: int) -> List[PlannedRun]:
    """Seeds outermost, so an interrupted matrix still leaves whole seeds to compare."""
    return [PlannedRun(task, arm, seed, position, len(arm_names))
            for seed in range(1, seeds + 1) for task in tasks
            for position, arm in enumerate(arm_order(arm_names, task.name, seed, order_seed), start=1)]
