"""What is run, and how the three conditions map onto it.

Two kinds of review are actually run for each (case, reviewer model):
  self   two calls in one session: restate the module, then review it
  fresh  one call in a new session: review it

The three conditions are views of those cells from the point of view of an "author" model X:
  self         = (X, self)
  fresh-same   = (X, fresh)
  fresh-cross  = (other model, fresh)
So a fresh review by Codex is both Claude's `fresh-cross` and Codex's `fresh-same`: it is the same
kind of event, and it is paid for once."""
from __future__ import annotations

import random
from typing import List, NamedTuple, Sequence

from .cases import Case
from .clients import REVIEWERS, other

SELF, FRESH = "self", "fresh"
MODES = (SELF, FRESH)
COND_SELF, COND_SAME, COND_CROSS = "self", "fresh-same", "fresh-cross"
CONDITIONS = (COND_SELF, COND_SAME, COND_CROSS)
RESTATE, REVIEW = "restate", "review"
MAX_ORDER_SEED = 2 ** 32


class Cell(NamedTuple):
    case: str
    reviewer: str
    mode: str
    sample: int

    @property
    def id(self) -> str:
        return f"{self.case}__{self.reviewer}__{self.mode}__n{self.sample}"

    @property
    def steps(self) -> Sequence[str]:
        return (RESTATE, REVIEW) if self.mode == SELF else (REVIEW,)


def new_order_seed() -> int:
    return random.SystemRandom().randrange(MAX_ORDER_SEED)


def make_plan(cases: Sequence[Case], samples: int, order_seed: int,
              reviewers: Sequence[str] = REVIEWERS) -> List[Cell]:
    """Every cell, shuffled from a recorded seed so that a run stopped early is not missing one
    reviewer or one mode in particular. The same inputs always give the same order."""
    cells = [Cell(case.name, reviewer, mode, sample)
             for sample in range(1, samples + 1) for case in cases for reviewer in reviewers for mode in MODES]
    return random.Random(f"reviewbench:{order_seed}").sample(cells, len(cells))


def planned_calls(cells: Sequence[Cell]) -> int:
    return sum(len(cell.steps) for cell in cells)


def condition_cell(author: str, condition: str, case: str, sample: int) -> Cell:
    """The cell that plays `condition` when `author` is the model whose work is under review."""
    if condition == COND_SELF:
        return Cell(case, author, SELF, sample)
    if condition == COND_SAME:
        return Cell(case, author, FRESH, sample)
    if condition == COND_CROSS:
        return Cell(case, other(author), FRESH, sample)
    raise ValueError(f"unknown condition: {condition!r}")
