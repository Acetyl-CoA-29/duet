from copy import deepcopy
from itertools import permutations

import pytest

from runqueue import CycleError, DisabledJob, Job, MissingJob, Plan, build_plan
from runqueue.preview import preview
from runqueue.worker import submission


def catalog(*jobs):
    return {job.name: job for job in jobs}


def test_transitive_diamond_and_repeated_edges_execute_once():
    jobs = catalog(Job("ship", ("link", "docs", "link")),
                   Job("link", ("compile", "compile")),
                   Job("docs", ("compile",)), Job("compile"))
    plan = build_plan(jobs, ["ship", "ship", "link"])
    assert plan.run == ("compile", "docs", "link", "ship")
    assert plan.reused == ()
    assert len(plan.run) == len(set(plan.run))


def test_ready_queue_is_global_not_depth_first():
    jobs = catalog(Job("a", ("z",)), Job("b"), Job("z"))
    assert build_plan(jobs, ["a", "b"]).run == ("b", "z", "a")


def test_newly_ready_job_competes_immediately():
    jobs = catalog(Job("a", ("b",)), Job("b"), Job("z"))
    assert build_plan(jobs, ["z", "a"]).run == ("b", "a", "z")


def test_order_is_independent_of_input_permutations():
    entries = [Job("a", ("z", "b")), Job("b"), Job("z"), Job("c")]
    for order in permutations(entries):
        for targets in (("a", "c"), ("c", "a")):
            assert build_plan(catalog(*order), targets).run == ("b", "c", "z", "a")
    reversed_edges = catalog(Job("a", ("b", "z")), *entries[1:])
    assert build_plan(reversed_edges, ["a", "c"]).run == ("b", "c", "z", "a")


def test_after_orders_separate_targets_without_expanding_selection():
    jobs = catalog(Job("a", after=("z",)), Job("z", ("base",)), Job("base"))
    assert build_plan(jobs, ["a"]).run == ("a",)
    assert build_plan(jobs, ["a", "z"]).run == ("base", "z", "a")


def test_after_applies_to_transitively_selected_job():
    jobs = catalog(Job("publish", ("a", "z")), Job("a", after=("z",)), Job("z"))
    assert build_plan(jobs, ["publish"]).run == ("z", "a", "publish")


def test_missing_disabled_and_broken_after_only_jobs_are_irrelevant():
    jobs = catalog(Job("work", after=("missing", "paused", "broken")),
                   Job("paused", enabled=False), Job("broken", ("absent",)))
    assert build_plan(jobs, ["work"]) == Plan(("work",), ())


def test_duplicate_hard_and_ordering_edges_share_one_constraint():
    jobs = catalog(Job("a", ("z", "z"), ("z", "z")), Job("z"))
    assert build_plan(jobs, ["a"]).run == ("z", "a")


def test_cache_boundary_ignores_historical_missing_dependency():
    jobs = catalog(Job("publish", ("bundle",)), Job("bundle", ("removed",)))
    assert build_plan(jobs, ["publish"], ["bundle"]) == Plan(("publish",), ("bundle",))
    assert build_plan(jobs, ["bundle"], ["bundle"]) == Plan((), ("bundle",))


def test_disabled_snapshot_is_usable_for_dependency_and_explicit_target():
    jobs = catalog(Job("publish", ("bundle",)),
                   Job("bundle", ("old",), enabled=False))
    assert build_plan(jobs, ["publish"], ["bundle"]) == Plan(("publish",), ("bundle",))
    assert build_plan(jobs, ["bundle"], ["bundle"]) == Plan((), ("bundle",))


def test_cache_cuts_dependency_and_ordering_cycles():
    jobs = catalog(Job("a", ("b",)), Job("b", ("a",), after=("b",)))
    assert build_plan(jobs, ["a"], ["b"]) == Plan(("a",), ("b",))
    assert build_plan(jobs, ["b"], ["b"]) == Plan((), ("b",))


def test_only_encountered_completed_jobs_are_reported_sorted():
    jobs = catalog(Job("publish", ("z", "a")), Job("a", ("old",)),
                   Job("z"), Job("unused"), Job("old"))
    completed = ["z", "unused", "gone", "a", "old", "z"]
    plan = build_plan(jobs, ["publish", "z"], completed)
    assert plan.run == ("publish",)
    assert plan.reused == ("a", "z")
    assert build_plan(jobs, [], completed) == Plan((), ())


def test_cached_after_target_is_not_selected_by_hint():
    jobs = catalog(Job("a", after=("z",)), Job("z", after=("a",)))
    assert build_plan(jobs, ["a"], ["z"]) == Plan(("a",), ())
    assert build_plan(jobs, ["a", "z"], ["z"]) == Plan(("a",), ("z",))


@pytest.mark.parametrize("completed", [(), ("absent",)])
def test_unknown_target_is_not_rescued_by_completed_set(completed):
    with pytest.raises(MissingJob) as error:
        build_plan({}, ["absent"], completed)
    assert error.value.name == "absent"


def test_missing_transitive_dependency_is_structured():
    with pytest.raises(MissingJob) as error:
        build_plan(catalog(Job("publish", ("middle",)), Job("middle", ("gone",))),
                   ["publish"])
    assert error.value.name == "gone"


def test_disabled_transitive_dependency_is_structured():
    with pytest.raises(DisabledJob) as error:
        build_plan(catalog(Job("publish", ("paused",)), Job("paused", enabled=False)),
                   ["publish"])
    assert error.value.name == "paused"


def test_unrelated_invalid_components_do_not_poison_partial_run():
    jobs = catalog(Job("ok"), Job("broken", ("missing",)),
                   Job("paused", enabled=False), Job("cycle", ("cycle",)))
    assert build_plan(jobs, ["ok"]) == Plan(("ok",), ())
    assert build_plan(jobs, []) == Plan((), ())


def test_hard_cycle_reports_downstream_blocked_jobs_not_completed_work():
    jobs = catalog(Job("a", ("b",)), Job("b", ("a",)), Job("tail", ("a",)),
                   Job("free"), Job("unused", ("unused",)))
    with pytest.raises(CycleError) as error:
        build_plan(jobs, ["tail", "free"])
    assert error.value.blocked == ("a", "b", "tail")


def test_ordering_cycle_is_checked_only_when_both_jobs_execute():
    jobs = catalog(Job("a", after=("b",)), Job("b", after=("a",)))
    assert build_plan(jobs, ["a"]) == Plan(("a",), ())
    with pytest.raises(CycleError) as error:
        build_plan(jobs, ["a", "b"])
    assert error.value.blocked == ("a", "b")


def test_combined_hard_and_ordering_cycle():
    jobs = catalog(Job("a", ("b",)), Job("b", after=("a",)))
    with pytest.raises(CycleError) as error:
        build_plan(jobs, ["a"])
    assert error.value.blocked == ("a", "b")


@pytest.mark.parametrize("job", [Job("self", ("self",)), Job("self", after=("self",))])
def test_self_edge_is_a_cycle(job):
    with pytest.raises(CycleError) as error:
        build_plan(catalog(job), ["self"])
    assert error.value.blocked == ("self",)


def test_inputs_are_not_mutated_and_plan_fields_are_tuples():
    jobs = catalog(Job("publish", ("base",)), Job("base"))
    targets, completed = ["publish", "publish"], ["base", "other"]
    original = deepcopy((jobs, targets, completed))
    plan = build_plan(jobs, targets, completed)
    assert (jobs, targets, completed) == original
    assert isinstance(plan.run, tuple)
    assert isinstance(plan.reused, tuple)


def test_single_use_iterables_are_supported():
    jobs = catalog(Job("publish", ("base",)), Job("base"))
    assert build_plan(jobs, iter(["publish", "publish"]), iter(["base"])) == (
        Plan(("publish",), ("base",)))


def test_no_state_leaks_after_success_or_failure():
    jobs = catalog(Job("a", ("b",)), Job("b", ("a",)), Job("ok"))
    assert build_plan(jobs, ["ok"]) == Plan(("ok",), ())
    with pytest.raises(CycleError):
        build_plan(jobs, ["a"])
    assert build_plan(jobs, ["a"], ["b"]) == Plan(("a",), ("b",))
    assert build_plan(jobs, ["ok"]) == Plan(("ok",), ())


def test_worker_and_preview_share_graph_and_cache_semantics():
    jobs = catalog(Job("ship", ("a", "z")), Job("a", after=("z",)),
                   Job("z", ("base",)), Job("base", enabled=False))
    request = {"targets": ["ship"], "completed": ["base", "unused"], "ticket": "r17"}
    before = deepcopy(request)
    assert preview(jobs, request["targets"], request["completed"]) == {
        "run": ["z", "a", "ship"], "reused": ["base"]}
    assert submission(jobs, request) == {
        "jobs": ["z", "a", "ship"], "reused": ["base"], "count": 3, "ticket": "r17"}
    assert request == before


def test_worker_legacy_defaults_still_resolve_dependencies():
    jobs = catalog(Job("a", ("z",)), Job("z", enabled=False))
    assert submission(jobs, {"completed": ["z"]}) == {
        "jobs": ["a"], "reused": ["z"], "count": 1, "ticket": None}
    assert submission(jobs, {"targets": [], "completed": ["z"], "ticket": ""}) == {
        "jobs": [], "reused": [], "count": 0, "ticket": ""}


@pytest.mark.parametrize("caller", [
    lambda jobs: preview(jobs, ["publish"]),
    lambda jobs: submission(jobs, {"targets": ["publish"]}),
])
def test_both_adapters_propagate_planning_errors(caller):
    with pytest.raises(MissingJob) as error:
        caller(catalog(Job("publish", ("missing",))))
    assert error.value.name == "missing"
    with pytest.raises(DisabledJob) as error:
        caller(catalog(Job("publish", ("paused",)), Job("paused", enabled=False)))
    assert error.value.name == "paused"
    with pytest.raises(CycleError) as error:
        caller(catalog(Job("publish", ("publish",))))
    assert error.value.blocked == ("publish",)


def test_response_lists_are_detached_and_repeatable():
    jobs = catalog(Job("publish", ("base",)), Job("base"))
    result = preview(jobs, ["publish"], ["base"])
    result["run"].append("injected")
    result["reused"].clear()
    assert preview(jobs, ["publish"], ["base"]) == {
        "run": ["publish"], "reused": ["base"]}
    request = {"targets": ["publish"], "completed": ["base"]}
    submitted = submission(jobs, request)
    submitted["jobs"].clear()
    submitted["reused"].clear()
    assert submission(jobs, request) == {
        "jobs": ["publish"], "reused": ["base"], "count": 1, "ticket": None}
