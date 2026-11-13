`invoice.py` contains one long function, `render_invoice(order)`, that has grown by accretion.
Refactor it so that it is made of small functions.

Requirements:

- No function in `invoice.py` may be longer than 20 lines (counted from its `def` line to its
  last line), and the module must define at least five functions. `tests/test_structure.py`
  checks this.
- `render_invoice(order)` must keep its name and signature and must return **exactly** the same
  string as it does today for every possible input, including inputs nobody thought about when
  the tests were written. This output is parsed by a downstream billing system, so any
  difference in what is printed, however small, is a regression. Inputs that raise an exception
  today must still raise the same type of exception.
- Do not "fix" behaviour that looks odd. If you think something is a bug, leave it as it is and
  mention it in your final message.

Only the standard library may be used.
