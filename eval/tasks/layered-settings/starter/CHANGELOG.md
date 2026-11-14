# Changes

## 0.9 — profile rollout (in progress)

Profiles are reusable *edits*, not complete configurations. Start with factory settings
once per service. Visit the service's listed profiles left to right, recursively visiting
each profile's own parents before applying that profile. A profile name contributes only
on its first visit within that service; shared ancestors and duplicate list entries do not
replay their edits. Finally apply the service's own layer. An active visit is a cycle, not
an already-applied profile. Start a new visit history for every service and every API call.

Runtime launch rows must use effective settings and omit disabled services. Operator report
rows must use the same effective settings but keep disabled services visible. Both keep
lexical service-name ordering and their existing row formats. Configuration errors must
propagate instead of silently dropping the affected service.

## 0.8 — stable

Inline settings, label deletion, null resets, and independent return values are supported.
