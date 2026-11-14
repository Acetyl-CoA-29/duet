# Configuration contract, revision 2

A layer has zero or more settings below and optional `extends`, a list of profile names.
Names are case-sensitive. An empty list means no parents. Profiles may themselves extend
profiles; a service can only extend profiles, never another service.

The effective settings always contain exactly these keys:

| Setting | Factory value | Effective value |
| --- | --- | --- |
| enabled | true | a boolean |
| workers | 2 | an integer greater than zero (not a boolean) |
| timeout | 30 | an integer greater than or equal to zero (not a boolean) |
| retry | {attempts: 2, delays: [1, 3]} | a mapping with the two fields below |
| retry.attempts | 2 | a nonnegative integer, not a boolean |
| retry.delays | [1, 3] | a list of nonnegative integers, not booleans |
| labels | {} | a mapping of string keys to string values |

Absent settings inherit. A present scalar replaces its inherited value; zero, false, and
empty strings are values, not absence. Lists replace as a whole, including an empty list.
`retry` merges by field. `labels` merges by key, and a null label value removes that key
(if absent, removal does nothing). An empty labels mapping therefore changes nothing.

A null setting resets that setting to its factory value, not its parent value. This applies
to scalar settings, the whole retry group, either retry field, and the whole labels map.
Resetting `retry` also discards any inherited retry subfields. A later layer can set new
subfields or labels after a reset. Neither `extends` nor profile names belong in the result.

Only the completed effective settings are type/range validated. A profile may temporarily
set workers to zero or a retry field to a placeholder, provided a descendant repairs it.
The layer shape must still be valid: mappings for retry/labels (or null), a list of strings
for extends, and known setting/retry keys only. Invalid shapes and invalid final values
raise ValueError. Do not require any relationship between attempts and the delay-list size.

Resolving an unknown service raises KeyError. A missing referenced profile or an inheritance
cycle raises ValueError, even if the service is disabled. Resolve only the requested
service's dependency closure: unused broken profiles and unrelated services do not affect
it. There is no requirement for exact error-message text.

Each call must return fresh nested containers, independent of the document, all other
returned results, and the factory defaults. This also applies to launch and report results.
