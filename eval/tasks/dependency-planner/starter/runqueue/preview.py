"""Adapter for the dashboard's planning endpoint."""

from .planner import build_plan


def preview(catalog, targets, completed=()):
    """A detached JSON-compatible representation of a plan."""
    plan = build_plan(catalog, targets, completed)
    return {"run": list(plan.run), "reused": list(plan.reused)}
