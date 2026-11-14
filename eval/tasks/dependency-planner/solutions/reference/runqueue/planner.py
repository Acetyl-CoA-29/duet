"""Resolve required artifacts, then schedule the selected executable graph."""

import heapq
from typing import Iterable, Mapping

from .model import CycleError, DisabledJob, Job, MissingJob, Plan


def _select(catalog, requested, completed):
    done = set(completed)
    pending = list(requested)
    selected = set()
    reused = set()
    while pending:
        name = pending.pop()
        if name in selected or name in reused:
            continue
        if name not in catalog:
            raise MissingJob(name)
        if name in done:
            reused.add(name)
            continue
        job = catalog[name]
        if not job.enabled:
            raise DisabledJob(name)
        selected.add(name)
        pending.extend(job.requires)
    return selected, reused


def _schedule(catalog, selected):
    waiting = {}
    followers = {name: set() for name in selected}
    for name in selected:
        job = catalog[name]
        prerequisites = (set(job.requires) | set(job.after)) & selected
        waiting[name] = len(prerequisites)
        for prerequisite in prerequisites:
            followers[prerequisite].add(name)
    ready = [name for name in selected if waiting[name] == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        name = heapq.heappop(ready)
        ordered.append(name)
        for follower in followers[name]:
            waiting[follower] -= 1
            if waiting[follower] == 0:
                heapq.heappush(ready, follower)
    if len(ordered) != len(selected):
        raise CycleError(selected - set(ordered))
    return tuple(ordered)


def build_plan(catalog: Mapping[str, Job], requested: Iterable[str],
               completed: Iterable[str] = ()) -> Plan:
    """Plan requested jobs under docs/planning.md's scheduling contract."""
    selected, reused = _select(catalog, requested, completed)
    return Plan(_schedule(catalog, selected), tuple(sorted(reused)))
