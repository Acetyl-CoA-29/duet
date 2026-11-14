"""Turn a queue request into the dispatcher payload.

The dispatcher sends jobs in this order and assumes all prerequisites are either
earlier in `jobs` or represented by a reused artifact. It never expands dependencies.
"""

from .model import DisabledJob, MissingJob


def submission(catalog, request):
    targets = request.get("targets", [name for name in catalog if catalog[name].enabled])
    completed = set(request.get("completed", ()))
    jobs = []
    reused = []
    for name in sorted(set(targets)):
        if name not in catalog:
            raise MissingJob(name)
        if name in completed:
            reused.append(name)
        elif not catalog[name].enabled:
            raise DisabledJob(name)
        else:
            jobs.append(name)
    return {"jobs": jobs, "reused": reused, "count": len(jobs),
            "ticket": request.get("ticket")}
