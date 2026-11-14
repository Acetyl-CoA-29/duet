# Service configuration

`servicecfg.resolver.resolve_service(document, name)` returns the effective settings for
one service. `servicecfg.launch.plan_launches(document)` produces worker launch records;
`servicecfg.report.service_report(document)` feeds the operator table. All are pure APIs:
they do not read files, change the supplied document, or retain caller-owned containers.

The JSON document has a `services` mapping and an optional `profiles` mapping. Each entry
is a layer. Configuration rules are in `docs/configuration.md`; rollout ordering is in
`CHANGELOG.md`. `schema.py` currently handles standalone layers. Keep existing imports
working; downstream users call these modules directly.

Run `python3 -m pytest -q -p no:cacheprovider tests` from this directory. This small test
suite includes the production compatibility cases and the first rollout smoke test.
