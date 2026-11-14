"""Public lookup API. Profile expansion belongs here during the 0.9 rollout."""
from .schema import read_inline


def resolve_service(document, name):
    """Resolve one service without inspecting unrelated configurations."""
    return read_inline(document["services"][name])
