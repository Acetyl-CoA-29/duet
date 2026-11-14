# Planning contract

## Choosing work

The requested names are entry points. Every selected job needs each name in its
`requires` before it can execute, so selection expands transitively through required
jobs. A shared prerequisite executes once. Repeated targets and repeated edges are
harmless. Requested and completed names may be any finite iterable, including a
single-use iterator. See the release notes for completed-job traversal boundaries.

`enabled=False` means a job cannot execute. A selected job without a reusable
snapshot that is disabled raises `DisabledJob`; a missing target or required job
raises `MissingJob`. Both exceptions expose that job's name as `.name`.
Only selected work is checked: an unrelated disabled job, broken dependency, or
cycle elsewhere in the catalog cannot prevent a partial run.

## Ordering

`after` is an ordering hint, not a request to produce an artifact. An entry adds
an edge only when both jobs will execute in this plan. It does not select that
other job, validate its existence, or traverse its prerequisites. If both jobs
are selected, the edge is mandatory, including when they came from separate targets.

Among all jobs ready at each step, execute the alphabetically smallest name (Python
string order). Newly ready jobs compete immediately with the other ready jobs.
The result must not depend on catalog insertion order, target order, or the order
of a job's dependency entries. This makes queue payloads reproducible.

If required and applicable `after` edges cannot be scheduled, raise `CycleError`.
Its `.blocked` tuple lists every selected executable job left unscheduled in name
order, including jobs waiting downstream of the cycle. Do not return a partial plan.
Edges out of a reused snapshot do not participate in cycle detection.

## Results and callers

`build_plan` returns a `Plan(run=tuple_of_names, reused=tuple_of_names)`. It does not
mutate the catalog, the job records, or input collections. There is no cross-request
cache: consecutive requests must reflect their own arguments, including after a
failed request. Exceptions propagate from preview and submission without translation.

Both `preview` and `submission` must use these same scheduling rules. Submission's
legacy default-target and ticket behavior is covered by `tests/test_pipeline.py`.
Keep those wire conventions while repairing selection and ordering.
