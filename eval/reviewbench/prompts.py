"""The two prompts a reviewer can receive. They are built from the module text and its description
only: never from the case's name, answer key, hidden tests or corrected version.

The review prompt is byte-for-byte the same in every condition. In the `self` condition it is sent
as the second message of a session whose first message was the restate prompt."""
from __future__ import annotations

from .cases import Case

MAX_ITEMS = 5
SHOWN_NAME = "module.py"        # every case is shown under the same name
RESTATE_WORDS = 150

NO_TOOLS = ("Everything you need is in this message. Do not read files, run commands or use any "
            "tools; answer from the text below alone.")

REVIEW_INSTRUCTIONS = f"""## Your review

Report only defects: places where the code, as written, behaves incorrectly, unsafely or contrary
to the description above for some input or situation. Do not report style, naming, missing
features, documentation or possible improvements. The module may have no defects; if you find
none, say so.

Answer in exactly this form and nothing else.

If you find no defects, one line:

DEFECTS: NONE

Otherwise the line `DEFECTS:` followed by a numbered list, most serious first, at most {MAX_ITEMS}
items, each on a single line that starts with the line number(s) concerned:

DEFECTS:
1. L<line> or L<first>-L<last>: one sentence saying what goes wrong and when.
"""


def numbered(module: str) -> str:
    """The module with a line number in front of every line, so line references are unambiguous."""
    lines = module.splitlines()
    width = len(str(len(lines)))
    return "\n".join(f"{number:>{width}} | {line}".rstrip() for number, line in enumerate(lines, start=1))


def _shown(case: Case) -> str:
    return ("## What the module is meant to do\n\n" + case.description + "\n\n"
            f"## The module (`{SHOWN_NAME}`), with line numbers\n\n```\n{numbered(case.module)}\n```\n")


def review_prompt(case: Case) -> str:
    return f"You are reviewing one Python module. {NO_TOOLS}\n\n{_shown(case)}\n{REVIEW_INSTRUCTIONS}"


def restate_prompt(case: Case) -> str:
    """First message of the `self` condition: the model takes the module in as its own subject
    before it is asked, in the same session, to review it."""
    return (f"You are reading one Python module. {NO_TOOLS}\n\n{_shown(case)}\n"
            "## Your answer\n\n"
            f"Explain in your own words, in at most {RESTATE_WORDS} words, what this module does and how "
            "each function goes about it. Describe it only; do not assess it.\n")
