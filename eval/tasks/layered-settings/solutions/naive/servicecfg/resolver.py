"""Expand profile inheritance recursively before applying local overrides."""
from copy import deepcopy

from .schema import DEFAULTS, apply_layer, validate


def resolve_service(document, name):
    profiles = document.get("profiles", {})

    def expand(layer, active):
        settings = deepcopy(DEFAULTS)
        parents = layer.get("extends", [])
        if not isinstance(parents, list) or any(not isinstance(p, str) for p in parents):
            raise ValueError("extends must be a list of names")
        for parent in parents:
            if parent in active:
                raise ValueError("inheritance cycle")
            if parent not in profiles:
                raise ValueError("missing profile: " + parent)
            inherited = expand(profiles[parent], active | {parent})
            apply_layer(settings, inherited)
        apply_layer(settings, layer)
        validate(settings)
        return settings

    return expand(document["services"][name], set())
