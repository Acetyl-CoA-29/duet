# Consumer integration

A browsing session describes the events present when its first page was read.
Persist the store's arrival high-water mark in continuation state: later arrivals
must not enter that session, even if their event time is far in the past. Deletions
take effect on every subsequent page read (already fetched pages are materialized).
A cursor must still work if its boundary row was
deleted; it describes an ordering position, not a reference to a surviving row.

Tokens are request-local data, not authorization credentials. The authenticated
tenant is always passed separately. Reusing a token with a different tenant or
kind raises CursorError; changing page size is allowed. CursorError is a ValueError
subclass and is also the public response to malformed or unsupported token data.
No signing, expiry or cross-database portability is required.

An export captures its session on first iteration, not when its generator object
is constructed. Callers can pause between yielded lines while ingestion continues.
Do not keep a database transaction open across yields. JSON formatting is stable:
one object per line, sorted keys, ensure_ascii=False, trailing newline. Unicode
messages are allowed. The stream has no header or footer.
