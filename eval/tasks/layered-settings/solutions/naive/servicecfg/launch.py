"""Convert settings into records consumed by the worker launcher."""
from .resolver import resolve_service


def plan_launches(document):
    """Return enabled services in name order; errors are caller-visible."""
    rows = []
    for name, layer in sorted(document["services"].items()):
        settings = resolve_service(document, name)
        if settings["enabled"]:
            rows.append({
                "service": name,
                "workers": settings["workers"],
                "timeout": settings["timeout"],
                "retry": settings["retry"],
                "labels": settings["labels"],
            })
    return rows
