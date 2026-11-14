"""Convert settings into records consumed by the worker launcher."""
from .schema import read_inline


def plan_launches(document):
    """Return enabled services in name order; errors are caller-visible."""
    rows = []
    for name, layer in sorted(document["services"].items()):
        settings = read_inline(layer)
        if settings["enabled"]:
            rows.append({
                "service": name,
                "workers": settings["workers"],
                "timeout": settings["timeout"],
                "retry": settings["retry"],
                "labels": settings["labels"],
            })
    return rows
