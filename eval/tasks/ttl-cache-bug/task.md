`cache.py` holds `TTLCache`, a small least-recently-used cache with expiry. It is in production
and users report that it throws away entries they had just read: the test
`tests/test_cache.py::test_recently_read_entry_survives` reproduces the report and fails.

Find and fix the cause. While you are in there, make sure the class really does everything its
docstring promises: the docstring is the contract and is correct, and we suspect the code
departs from it in more than one place. Fix every departure you find.

Do not change the public interface (constructor arguments, method names, the `clock` hook) and
do not change the docstring's meaning. Only the standard library may be used.
