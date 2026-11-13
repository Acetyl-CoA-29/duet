Implement `allocate(total_cents, weights)` in `billing.py`. It splits an amount of money between
participants in proportion to their weights and returns a list of whole-cent shares, one per
weight, in the same order.

Requirements:

- The shares always add up to `total_cents` exactly. No cent is lost or invented.
- Each participant first gets their exact proportional share rounded down to a whole cent. The
  cents left over are then handed out one at a time to the participants whose exact share had
  the largest fractional part. When fractional parts are equal, the participant earlier in the
  list comes first.
- A participant with weight 0 always gets 0.
- `total_cents` may be negative (a refund). Allocate the absolute value by the rules above, then
  negate every share.
- Totals can be very large (well beyond 2**53 cents), and the result must still be exact.
- `total_cents` and every weight must be an `int` (`bool` does not count); otherwise raise
  `TypeError`. A negative weight, an empty list of weights, or weights that are all zero raise
  `ValueError`.
- The `weights` list passed in must not be modified.

Only the standard library may be used.
