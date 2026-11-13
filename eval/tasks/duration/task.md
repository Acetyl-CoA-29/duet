Implement two functions in `duration.py`. They read and write the duration strings used in our
job configuration files, where a typo must be rejected rather than silently misread.

`parse_duration(text) -> int` returns the number of seconds.

- A duration is one or more components written with nothing between them. A component is a
  whole number followed by one unit: `d` (days), `h` (hours), `m` (minutes), `s` (seconds).
  Examples: `45s`, `1h30m`, `2d4h`, `0s`.
- Components must appear from the largest unit to the smallest, and a unit may appear at most once.
  `30m1h` and `1h1h` are invalid.
- Numbers are ASCII digits `0-9` only: no sign, no decimal point, no digits from other scripts.
  A number may be larger than the next unit (`90m` is valid and means 5400).
- Units are lowercase. A bare number such as `90` is invalid.
- Whitespace around the whole string is ignored; whitespace anywhere inside is invalid.
- Anything else, including the empty string, raises `ValueError`. A value that is not a `str`
  raises `TypeError`.

`format_duration(seconds) -> str` is the inverse.

- It uses the largest units possible and leaves out components that are zero: `5400` gives
  `1h30m`, `86400` gives `1d`.
- `0` gives `0s`.
- A negative number raises `ValueError`. Anything that is not an `int` (and `bool` does not
  count as an `int` here) raises `TypeError`.

Only the standard library may be used.
