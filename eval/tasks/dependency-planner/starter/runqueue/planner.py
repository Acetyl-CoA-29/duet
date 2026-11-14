"""Construct a reusable plan; no execution takes place in this module."""

from typing import Iterable, Mapping

from .model import DisabledJob, Job, MissingJob, Plan


def build_plan(catalog: Mapping[str, Job], requested: Iterable[str],
               completed: Iterable[str] = ()) -> Plan:
    """Plan requested jobs under docs/planning.md's scheduling contract.

    The original callers sent an already-expanded, ordered list of work. The
    dashboard now sends entry points; this old implementation still assumes
    selection and dependency handling have happened before this call.
    """
    done = set(completed)
    run = []
    reused = []
    for name in sorted(set(requested)):
        if name not in catalog:
            raise MissingJob(name)
        if name in done:
            reused.append(name)
        elif not catalog[name].enabled:
            raise DisabledJob(name)
        else:
            run.append(name)
    return Plan(tuple(run), tuple(reused))
