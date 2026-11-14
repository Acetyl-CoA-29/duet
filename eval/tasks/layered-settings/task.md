Finish the profile rollout in this service configuration package. Operations has started
sharing configuration between services, but the resolver, launch planner, and operator
report still behave as though every service were self-contained.

Implement profile-aware resolution and wire it into both consumers. Preserve the existing
public functions and their output formats. The workspace contains the configuration
contract, rollout notes, and compatibility tests; use those as the requirements. Do not
change the tests. Production code must use Python 3.9's standard library only.

Acceptance: `python3 -m pytest -q -p no:cacheprovider tests`
