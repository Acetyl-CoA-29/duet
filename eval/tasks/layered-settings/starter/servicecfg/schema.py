"""Standalone layer operations shared by the existing consumers."""
from copy import deepcopy

DEFAULTS = {
    "enabled": True,
    "workers": 2,
    "timeout": 30,
    "retry": {"attempts": 2, "delays": [1, 3]},
    "labels": {},
}


def apply_layer(settings, layer):
    """Apply one layer in place. Validation of effective values is separate."""
    if not isinstance(layer, dict):
        raise ValueError("layer must be a mapping")
    if set(layer) - (set(DEFAULTS) | {"extends"}):
        raise ValueError("unknown setting")
    parents = layer.get("extends", [])
    if not isinstance(parents, list) or any(not isinstance(p, str) for p in parents):
        raise ValueError("extends must be a list of names")
    for key, value in layer.items():
        if key == "extends":
            continue
        if value is None:
            settings[key] = deepcopy(DEFAULTS[key])
        elif key == "retry":
            if not isinstance(value, dict) or set(value) - set(DEFAULTS["retry"]):
                raise ValueError("invalid retry fields")
            for field, setting in value.items():
                settings[key][field] = deepcopy(
                    DEFAULTS[key][field] if setting is None else setting)
        elif key == "labels":
            if not isinstance(value, dict):
                raise ValueError("labels must be a mapping")
            for label, setting in value.items():
                if setting is None:
                    settings[key].pop(label, None)
                else:
                    settings[key][label] = deepcopy(setting)
        else:
            settings[key] = deepcopy(value)


def validate(settings):
    """Validate the completed settings; booleans are not numeric settings."""
    if type(settings["enabled"]) is not bool:
        raise ValueError("enabled must be a boolean")
    fields = [(settings["workers"], 1), (settings["timeout"], 0),
              (settings["retry"]["attempts"], 0)]
    if any(type(value) is not int or value < minimum for value, minimum in fields):
        raise ValueError("invalid numeric setting")
    delays = settings["retry"]["delays"]
    if not isinstance(delays, list) or any(type(x) is not int or x < 0 for x in delays):
        raise ValueError("invalid retry delays")
    if any(not isinstance(k, str) or not isinstance(v, str)
           for k, v in settings["labels"].items()):
        raise ValueError("invalid label")


def read_inline(layer):
    """Compatibility reader for one standalone layer."""
    settings = deepcopy(DEFAULTS)
    apply_layer(settings, layer)
    validate(settings)
    return settings
