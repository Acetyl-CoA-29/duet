"""Expand dashboard entry points into a dependency-first plan."""

from typing import Iterable, Mapping

from .model import CycleError, DisabledJob, Job, MissingJob, Plan


def build_plan(catalog: Mapping[str, Job], requested: Iterable[str],
               completed: Iterable[str] = ()) -> Plan:
    done = set(completed)
    visited = set()
    active = set()
    ordered = []
    reused = set()

    def visit(name):
        if name in visited:
            return
        if name in active:
            raise CycleError(active)
        if name not in catalog:
            raise MissingJob(name)
        job = catalog[name]
        if not job.enabled:
            raise DisabledJob(name)
        if name in done:
            reused.add(name)
            visited.add(name)
            return
        active.add(name)
        for dependency in sorted(set(job.requires)):
            visit(dependency)
        active.remove(name)
        visited.add(name)
        ordered.append(name)

    for target in sorted(set(requested)):
        visit(target)
    return Plan(tuple(ordered), tuple(sorted(reused)))
