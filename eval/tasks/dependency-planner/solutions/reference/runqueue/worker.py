"""Turn a queue request into the dispatcher's dependency-ordered payload."""

from .planner import build_plan


def submission(catalog, request):
    targets = request.get("targets", [name for name in catalog if catalog[name].enabled])
    plan = build_plan(catalog, targets, request.get("completed", ()))
    return {"jobs": list(plan.run), "reused": list(plan.reused), "count": len(plan.run),
            "ticket": request.get("ticket")}
