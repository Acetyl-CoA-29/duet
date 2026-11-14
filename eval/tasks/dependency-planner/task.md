# Partial runs omit build prerequisites

The release dashboard now submits a target such as `publish` instead of listing every
job in the pipeline. The preview currently lists only that target, and the worker can
enqueue it without the artifacts it needs. Restore dependency-aware planning for both
preview and submission.

Keep the public functions and result shapes compatible. This is a scheduling repair,
not a change to the pipeline format or artifact-cache policy. Read the repository's
planning documentation, release notes, and existing compatibility tests before choosing
an implementation. The package must remain Python 3.9 compatible and standard-library
only. Do not edit the existing tests.

Acceptance: `python3 -m pytest -q -p no:cacheprovider tests`
