import pytest

from runqueue import DisabledJob, Job, MissingJob, Plan, build_plan
from runqueue.preview import preview
from runqueue.worker import submission


def catalog(*jobs):
    return {job.name: job for job in jobs}


def test_release_preview_includes_prerequisites():
    jobs = catalog(Job("publish", ("bundle",)), Job("bundle", ("compile",)),
                   Job("compile"))
    assert preview(jobs, ["publish"]) == {
        "run": ["compile", "bundle", "publish"], "reused": []}


def test_plan_value_for_independent_work():
    jobs = catalog(Job("zip"), Job("assets"))
    assert build_plan(jobs, ["zip", "assets", "zip"]) == Plan(("assets", "zip"), ())


def test_reused_target_is_reported():
    assert preview(catalog(Job("build")), ["build"], ["build"]) == {
        "run": [], "reused": ["build"]}


def test_missing_name_error_is_structured():
    with pytest.raises(MissingJob) as error:
        build_plan({}, ["absent"])
    assert error.value.name == "absent"


def test_disabled_target_fails():
    with pytest.raises(DisabledJob):
        build_plan(catalog(Job("paused", enabled=False)), ["paused"])


def test_worker_default_targets_are_enabled_jobs():
    jobs = catalog(Job("zip"), Job("paused", enabled=False), Job("assets"))
    assert submission(jobs, {"ticket": "batch-17"}) == {
        "jobs": ["assets", "zip"], "reused": [], "count": 2, "ticket": "batch-17"}


def test_worker_empty_targets_do_not_mean_default_targets():
    result = submission(catalog(Job("build")), {"targets": [], "ticket": ""})
    assert result == {"jobs": [], "reused": [], "count": 0, "ticket": ""}


def test_worker_ticket_is_optional_and_cache_does_not_count_as_work():
    result = submission(catalog(Job("build")), {"completed": ["build"]})
    assert result == {"jobs": [], "reused": ["build"], "count": 0, "ticket": None}
