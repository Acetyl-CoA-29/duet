"""Operator table rows. Disabled services are retained for auditing."""
from .resolver import resolve_service


def service_report(document):
    rows = []
    for name, layer in sorted(document["services"].items()):
        settings = resolve_service(document, name)
        rows.append({
            "service": name,
            "status": "ready" if settings["enabled"] else "disabled",
            "workers": settings["workers"],
            "timeout": settings["timeout"],
            "retry_attempts": settings["retry"]["attempts"],
            "labels": settings["labels"],
        })
    return rows
