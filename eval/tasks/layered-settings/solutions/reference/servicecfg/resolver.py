"""Resolve a service by applying profile contributions in first-visit order."""
from copy import deepcopy

from .schema import DEFAULTS, apply_layer, validate


def resolve_service(document, name):
    service = document["services"][name]
    profiles = document.get("profiles", {})
    settings = deepcopy(DEFAULTS)
    applied = set()
    active = set()

    def parents(layer):
        # Shape errors must be caught even when no parent is traversed.
        if not isinstance(layer, dict):
            raise ValueError("layer must be a mapping")
        names = layer.get("extends", [])
        if not isinstance(names, list) or any(not isinstance(p, str) for p in names):
            raise ValueError("extends must be a list of names")
        return names

    def visit(profile):
        if profile in active:
            raise ValueError("inheritance cycle")
        if profile in applied:
            return
        if profile not in profiles:
            raise ValueError("missing profile: " + profile)
        active.add(profile)
        layer = profiles[profile]
        for parent in parents(layer):
            visit(parent)
        apply_layer(settings, layer)
        active.remove(profile)
        applied.add(profile)

    for profile in parents(service):
        visit(profile)
    apply_layer(settings, service)
    validate(settings)
    return settings
