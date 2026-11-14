# Export protocol, version 1

## Wire representation

An export is UTF-8. Each nonblank physical line is a JSON object, with JSON parsing
semantics provided by Python's standard `json.loads`. LF is the sole record delimiter;
CRLF is accepted as well. No other Unicode or ASCII character ends a record. Blank
lines consist only of spaces, horizontal tabs, and carriage returns and are ignored.
Physical line numbers start at 1 and include ignored lines. A record's `line` refers
to that physical line in the entire export, not to its transport chunk.

The final record need not end in LF. Empty exports and trailing blank lines are legal.
Every transport adapter may split anywhere, including inside a UTF-8 character, CRLF,
or a record. Empty chunks are keepalives, not end-of-file. A decoder must retain an
unfinished line between calls and emit only complete lines until end-of-file.

## Decoder lifecycle

`RecordDecoder.feed(chunk: bytes) -> list[Record]` accepts one transport chunk.
`finish() -> list[Record]` signals end-of-file and emits any final unterminated record.
A successful finish is idempotent: later finishes return an empty list. Feeding a
successfully finished decoder, even an empty chunk, raises `RuntimeError`.

Malformed UTF-8, invalid JSON, and JSON values that are not objects raise `RecordError`.
Its `line` is the failing physical line and `kind` is respectively `utf8`, `json`, or
`object`; the message follows the constructor in `model.py`. Do not leak the raw
export contents into the exception message. Detect errors only when a line is complete
or finish is called: a transport prefix is not yet a record.

An error makes the decoder terminal. Later calls to feed or finish re-raise the same
`RecordError` instance. Each feed is a delivery transaction: if any complete line in
that call is invalid, it returns no records from that call. Previously returned records
remain delivered. Consumers must not publish a valid prefix of a failing call.

## Consumers

The finite iterator and file adapter finalize on normal exhaustion; they must not fetch
the next transport chunk before delivering all records produced by the current one.
The live endpoint finalizes only on close. All consumers preserve the decoder's line
numbers, record order, validation, and error identity. A source iterator exception is
propagated as-is; it is not an end-of-file signal. File objects remain owned by callers.
