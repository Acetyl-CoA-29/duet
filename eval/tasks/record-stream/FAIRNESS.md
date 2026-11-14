# Author notes: record-stream

This file stays outside `starter/` and is not supplied to an evaluated arm.

The ticket describes a production streaming migration without listing edge cases. The
engineer must reconcile the decoder contract, old exporter compatibility, two different
consumer lifecycles, and existing dashboard behavior. The starter is intentionally a
former whole-line parser whose assumptions no longer hold for transport chunks.

| Oracle requirement | Evidence supplied to every arm |
| --- | --- |
| Arbitrary chunk splits, including UTF-8 and CRLF; keepalive chunks | `docs/protocol.md`, Wire representation; visible ASCII chunk regression |
| LF alone delimits; line numbers include blanks | `docs/protocol.md`, Wire representation; visible line-number test |
| Blank means only SP, HT, CR; Unicode whitespace is not blank | `docs/protocol.md`, Wire representation |
| Final unterminated record; empty exports; finish idempotence | `docs/protocol.md`, Wire representation and Decoder lifecycle |
| BOM accepted once at byte zero, not at subsequent lines | `CHANGELOG.md`, 1.2 compatibility entry |
| Standard `json.loads` semantics, object requirement, extension fields | `docs/protocol.md`; `report.py` module docstring |
| Error categories, physical line numbers, content-free message | `docs/protocol.md`, Decoder lifecycle; `model.py`; visible error test |
| Decode only complete records or EOF; malformed prefix may become valid | `docs/protocol.md`, Decoder lifecycle |
| Sticky error object across feed and finish | `docs/protocol.md`, Decoder lifecycle |
| Feed delivery atomicity; earlier pushes retained | `docs/protocol.md`; `upload.py` push docstring; `CHANGELOG.md` 1.1 |
| Batch and file EOF; live close EOF | `README.md` entry-point map; `docs/protocol.md`, Consumers; API docstrings |
| Source read laziness and source exception propagation | `docs/protocol.md`, Consumers |
| File starts at current position, stays open; positive chunk size | `readers.py` docstring and existing validation; visible file test |
| Independent upload snapshots and repeated closes | `CHANGELOG.md` 1.2; visible snapshot test |
| Record frozen metadata, mutable JSON dictionary | `CHANGELOG.md` 1.2; `model.py`; visible record test |
| Dashboard category order, empty-string categories, unknown fallback | Existing visible dashboard test and `report.py` implementation |

The reference changes the decoder, finite iterator, and upload close. The naive solution
is a credible byte-buffer repair: it handles chunking and batch EOF, but keeps line-local
BOM stripping, skips all Unicode whitespace, permits recovery after a parse error, and
does not migrate the live-close path. It passes every supplied acceptance test.

Estimated solo oracle pass rate: **45%**, uncalibrated. This is a hypothesis, not evidence
of model difficulty or of duet improvement; calibrate with independent solo runs before
using this task to support comparisons. No real model runs were used while authoring it.
