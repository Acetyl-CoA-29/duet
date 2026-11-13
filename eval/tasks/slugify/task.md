Implement `slugify(text, max_length=None)` in `slug.py`. It turns a title into a URL slug.

Rules, in this order:

1. `text` must be a `str`, otherwise raise `TypeError`.
2. `max_length`, when given, must be an `int` greater than zero, otherwise raise `ValueError`.
3. Apostrophes (`'` and `’`) are removed, so `don't` becomes `dont`.
4. Accented Latin letters lose their accents (`é` becomes `e`, `ü` becomes `u`) and compatibility
   characters are expanded (the ligature `ﬁ` becomes `fi`), using Unicode NFKD normalisation.
   Anything that is still not ASCII after that counts as a separator.
5. Letters are lowercased.
6. Every run of characters other than `a-z` and `0-9` becomes a single hyphen.
7. The result never starts or ends with a hyphen.
8. With `max_length`, the result is at most that many characters, and cutting it must not leave
   a hyphen at the end.
9. If nothing is left, return `"n-a"`.

Only the standard library may be used.
