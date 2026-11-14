# Release history

## 1.3

- Transport uploads may now arrive as arbitrarily sized chunks rather than one line
  at a time. The dashboard and public record objects remain compatible with 1.2.

## 1.2

- Exports from the desktop client may contain one UTF-8 byte-order mark at absolute
  byte offset zero. Ignore that marker, including when the first physical line is
  otherwise blank. A byte-order mark anywhere else is ordinary content, not a marker.
- Repeating a successful upload close returns a fresh list containing the same
  records. Mutating a returned list does not mutate the upload's stored records.
- Record metadata is immutable, but JSON dictionaries are still passed through.

## 1.1

- Added the dashboard summary and binary-file adapter.
- Upload errors retain successfully delivered earlier pushes for diagnostics.
