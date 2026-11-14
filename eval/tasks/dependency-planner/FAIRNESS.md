# Author notes: dependency-planner

This file stays outside `starter/` and is never given to an evaluated arm.

## Intended difficulty

The ticket sounds like a missing recursive expansion. A correct repair instead needs
to separate selection from scheduling, reconcile two different kinds of edges with
artifact boundaries, and replace a second independently implemented call path. A
depth-first traversal can satisfy the visible regression while returning a valid
topological order that violates the existing deterministic readiness contract.

Estimated pooled solo hidden-suite pass rate: **45%**, an uncalibrated judgment.
Only repeated solo trials can establish the actual rate; no model calls were made
while authoring. Do not describe this estimate as measured discrimination.

## Visible evidence for every oracle check

Test names below omit their `test_` prefix. All paths in the evidence column are
relative to `starter/`; every cited source is available to every evaluated arm.

| Oracle test(s) | Model-visible evidence |
| --- | --- |
| `transitive_diamond_and_repeated_edges_execute_once` | `docs/planning.md`, Choosing work: transitive required selection, shared prerequisites once, repeated targets and edges harmless; `tests/test_pipeline.py` has the failing chain regression. |
| `ready_queue_is_global_not_depth_first`, `newly_ready_job_competes_immediately`, `order_is_independent_of_input_permutations` | `docs/planning.md`, Ordering: smallest ready name at each step, newly ready jobs compete immediately, all three input orders irrelevant. |
| `after_orders_separate_targets_without_expanding_selection`, `after_applies_to_transitively_selected_job` | `docs/planning.md`, Ordering: `after` is mandatory when both endpoints execute, including across targets, but does not select work. |
| `missing_disabled_and_broken_after_only_jobs_are_irrelevant` | The same paragraph says ordering hints neither validate the other name nor traverse its prerequisites. |
| `duplicate_hard_and_ordering_edges_share_one_constraint` | Choosing work says repeated edges are harmless; Ordering defines both kinds as precedence edges, not multiplicities. |
| `cache_boundary_ignores_historical_missing_dependency` | `CHANGELOG.md` 1.4: a completed artifact ends traversal and old prerequisites need not exist. |
| `disabled_snapshot_is_usable_for_dependency_and_explicit_target` | `CHANGELOG.md` 1.4 explicitly permits reuse when disabled, including explicit targets. |
| `cache_cuts_dependency_and_ordering_cycles` | `CHANGELOG.md` 1.4 ends traversal at snapshots; `docs/planning.md` excludes their outgoing edges from cycles. |
| `only_encountered_completed_jobs_are_reported_sorted` | `CHANGELOG.md` 1.4 defines reached-only, unique, sorted reuse reporting, unrelated cache names ignored, and empty-request behavior. |
| `cached_after_target_is_not_selected_by_hint` | Ordering only applies when both endpoints execute; release notes only report encountered completed jobs. |
| `unknown_target_is_not_rescued_by_completed_set` (2 cases) | `CHANGELOG.md` says the snapshot job itself must still exist; `model.py` defines `.name` on `MissingJob`. |
| `missing_transitive_dependency_is_structured`, `disabled_transitive_dependency_is_structured` | `docs/planning.md`, Choosing work: explicit errors for missing or disabled required work and their `.name` fields. |
| `unrelated_invalid_components_do_not_poison_partial_run` | Choosing work explicitly excludes validation of unselected components, including unrelated cycles. |
| `hard_cycle_reports_downstream_blocked_jobs_not_completed_work` | Ordering defines `.blocked` as all unscheduled selected executable jobs, sorted, including jobs downstream of cycles. |
| `ordering_cycle_is_checked_only_when_both_jobs_execute`, `combined_hard_and_ordering_cycle`, `self_edge_is_a_cycle` (2 cases) | Ordering says both required and applicable `after` edges participate in scheduling and cycles; no edge excludes its own node. |
| `inputs_are_not_mutated_and_plan_fields_are_tuples` | `docs/planning.md`, Results and callers: explicit nonmutation and tuple result contract; `model.py` contains the immutable `Plan`. |
| `single_use_iterables_are_supported` | Choosing work explicitly permits finite single-use iterators for requested and completed names. |
| `no_state_leaks_after_success_or_failure` | Results and callers explicitly says no cross-request cache and includes calls after a failed request. |
| `worker_and_preview_share_graph_and_cache_semantics` | Ticket explicitly covers both paths; Results and callers requires identical rules and nonmutation; `worker.py` says the dispatcher never expands prerequisites. |
| `worker_legacy_defaults_still_resolve_dependencies` | Visible compatibility tests distinguish absent targets (all enabled jobs) from empty targets (none), keep an empty ticket, and exclude reused artifacts from `count`; docs require the same planning rules for both callers. |
| `both_adapters_propagate_planning_errors` (2 cases) | Results and callers explicitly requires propagation without translation; structured exception classes are public in `runqueue/__init__.py`. |
| `response_lists_are_detached_and_repeatable` | `CHANGELOG.md` 1.3 says preview exposes detached lists; `worker.py` constructs fresh payload lists; Results and callers forbids cross-request state. |

The oracle does not assert implementation structure, error message text, which of
multiple independent input errors must be raised first, unsupported record types,
performance thresholds, recursion depth, or any filesystem/environment behavior.

## Author fixtures and verification

- Starter: 233 lines in nine files. Its public interface, legacy adapters, and error
  records are usable; dependency expansion is absent, so one visible test fails.
- Reference: replaces `planner.py` with explicit closure plus a priority-queue
  topological scheduler, and routes `worker.py` through it. No test changes.
- Naive: changes only `planner.py` to a plausible sorted recursive DFS with cycle
  detection and cache handling. It fixes the visible chain but misses global ready
  ordering, `after`, disabled snapshot precedence, and the legacy worker path.
- Oracle: 31 collected cases and 50 explicit assertion statements, with additional
  permutation checks for stable ordering. Python 3.9 compatible; library code uses
  only the standard library. Tests run through the harness's existing pytest setup.

`python3 eval/run_eval.py verify --tasks dependency-planner` reports:

| Fixture | Visible | Hidden |
| --- | --- | --- |
| Starter | fail | 6 pass / 25 fail |
| Reference | pass | 31 pass / 0 fail |
| Naive | pass | 18 pass / 13 fail |

Verification used a temporary `DUET_HOME` and made no external model calls.
