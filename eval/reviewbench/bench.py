"""Run the planned cells under hard caps, one record per cell, so that a stopped run can be resumed
without repeating a call that already produced a reply.

Caps are checked before a cell starts, against every call the session has ever made (the ledger):
  calls    a cell starts only if all of its remaining calls fit under `max_calls`
  dollars  a Claude cell starts only if its remaining calls, each at the per-call cap, fit under
           `max_total_cost`. Codex reports no dollars, so only the call cap bounds it.
A run that meets a cap stops cleanly and records which cells were left undone."""
from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import clients, store
from .cases import Case
from .plan import RESTATE, REVIEW, SELF, Cell
from .prompts import restate_prompt, review_prompt

Emit = Callable[[str], None]
CENT_FRACTION = 1e-9                  # sums of reported dollars are floats; do not trip the cap on rounding


@dataclass(frozen=True)
class RunOptions:
    session_dir: Path
    work_root: Path                       # empty per-cell folders are made here, away from the cases
    client: clients.ClientOptions
    max_calls: int
    max_total_cost: Optional[float] = None
    retry_errors: bool = False


@dataclass(frozen=True)
class Outcome:
    calls: int                            # calls started by this invocation
    stopped: Optional[str]                # why the run ended before the plan did; None when it did not
    undone: Tuple[str, ...]               # cells not attempted because the run stopped
    failed: Tuple[str, ...]               # cells that ended in an error (this invocation or left from earlier)


def can_continue(record: Optional[store.Record]) -> bool:
    """True for a `self` cell whose restate call succeeded but whose review has not: its session can
    be resumed, so the restate call need not be paid for again."""
    return bool(record and record.get("mode") == SELF and record.get("status") != store.OK
                and record.get("restated") and record.get("session") and record.get("workdir"))


def remaining_steps(cell: Cell, record: Optional[store.Record]) -> Sequence[str]:
    if record is not None and record.get("status") == store.OK:
        return ()
    return (REVIEW,) if can_continue(record) else cell.steps


def cap_reason(cell: Cell, needed: int, ledger: List[Dict[str, Any]], options: RunOptions) -> Optional[str]:
    """Why this cell may not start, or None when it fits under both caps."""
    made = store.calls_made(ledger)
    if made + needed > options.max_calls:
        return f"call cap: {made} call(s) made, the next cell needs {needed}, --max-calls is {options.max_calls}"
    if options.max_total_cost is None or cell.reviewer != clients.CLAUDE:
        return None
    spent = store.dollars_reported(ledger)
    headroom = needed * (options.client.max_call_cost or 0.0)
    if spent >= options.max_total_cost or spent + headroom > options.max_total_cost + CENT_FRACTION:
        return (f"dollar cap: ${spent:.2f} reported so far; the next Claude cell could add up to "
                f"${headroom:.2f}, and --max-total-cost is ${options.max_total_cost:.2f}")
    return None


def _call_entry(cell: Cell, step: str, result: clients.CallResult) -> Dict[str, Any]:
    return {"cell": cell.id, "reviewer": cell.reviewer, "step": step, "ok": result.ok,
            "cost_usd": result.cost_usd, "tokens": result.tokens, "seconds": result.seconds,
            "exit_code": result.exit_code, "timed_out": result.timed_out, "error": result.error,
            "leaks": list(result.leaks), "commands_run": result.commands_run,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def _record(cell: Cell, status: str, calls: Sequence[Dict[str, Any]], **fields: Any) -> store.Record:
    return {"version": store.RECORD_VERSION, "cell": cell.id, "case": cell.case, "reviewer": cell.reviewer,
            "mode": cell.mode, "sample": cell.sample, "status": status, "calls": list(calls),
            "error": "", "reply": "", "restated": False, "session": None, "workdir": None, **fields}


def run_cell(cell: Cell, case: Case, options: RunOptions, earlier: Optional[store.Record] = None) -> store.Record:
    """Make this cell's remaining calls and write its record after each one. Every call is added to
    the ledger whether or not it worked, because a failed call may still have been paid for."""
    resumed = can_continue(earlier)
    steps = remaining_steps(cell, earlier)
    calls: List[Dict[str, Any]] = list(earlier.get("calls", [])) if earlier else []
    session: Optional[str] = earlier["session"] if resumed and earlier else None
    workdir = Path(earlier["workdir"]) if resumed and earlier else options.work_root / uuid.uuid4().hex[:12]
    kept = {"restated": resumed, "session": session, "workdir": str(workdir)}
    record = _record(cell, store.ERROR, calls, error="no call was made", **kept)
    for step in steps:
        prompt = restate_prompt(case) if step == RESTATE else review_prompt(case)
        result = clients.call(cell.reviewer, prompt, workdir, options.session_dir / store.LOGS / cell.id,
                              f"{len(calls) + 1}-{step}", options.client, session)
        entry = _call_entry(cell, step, result)
        store.append_ledger(options.session_dir, entry)
        calls = [*calls, entry]
        error = result.error or ("" if step == REVIEW or result.session else "the client gave no session id to resume")
        if error:
            record = _record(cell, store.ERROR, calls, error=f"{step}: {error}", **kept)
            break
        if step == RESTATE:
            session = result.session
            kept = {"restated": True, "session": session, "workdir": str(workdir)}
            record = _record(cell, store.PARTIAL, calls, **kept)
        else:
            record = _record(cell, store.OK, calls, reply=result.reply, **{**kept, "session": result.session or session})
        store.write_record(options.session_dir, record)
    store.write_record(options.session_dir, record)
    if not can_continue(record):
        shutil.rmtree(str(workdir), ignore_errors=True)
    return record


def _skip(record: Optional[store.Record], retry_errors: bool) -> bool:
    """Done cells are never repeated. A cell that ended in an error is left alone unless asked."""
    if record is None:
        return False
    status = record.get("status")
    return status == store.OK or (status == store.ERROR and not retry_errors)


def _line(index: int, total: int, record: store.Record) -> str:
    cost = sum(call.get("cost_usd") or 0.0 for call in record["calls"])
    detail = f"  {record['error']}" if record["status"] != store.OK else ""
    return f"[{index}/{total}] {record['cell']}: {record['status']} (${cost:.2f} reported so far for this cell){detail}"


def run_plan(cells: Sequence[Cell], cases: Mapping[str, Case], options: RunOptions, emit: Emit) -> Outcome:
    """Run every cell that is not already done, in plan order, until the plan or a cap ends the run."""
    records = store.load_records(options.session_dir)
    started = 0
    stopped: Optional[str] = None
    undone: Tuple[str, ...] = ()
    for index, cell in enumerate(cells, start=1):
        earlier = records.get(cell.id)
        if _skip(earlier, options.retry_errors):
            continue
        needed = len(remaining_steps(cell, earlier))
        stopped = cap_reason(cell, needed, store.load_ledger(options.session_dir), options)
        if stopped is None:
            try:
                records = {**records, cell.id: run_cell(cell, cases[cell.case], options, earlier)}
            except KeyboardInterrupt:
                stopped = "interrupted; the call in flight was stopped and may still have been charged"
        if stopped is not None:
            undone = tuple(later.id for later in cells[index - 1:]
                           if (records.get(later.id) or {}).get("status") != store.OK)
            emit(f"Stopping before {cell.id}: {stopped}")
            break
        started += len(records[cell.id]["calls"]) - len((earlier or {}).get("calls", []))
        emit(_line(index, len(cells), records[cell.id]))
    failed = tuple(cell.id for cell in cells if (records.get(cell.id) or {}).get("status") == store.ERROR)
    return Outcome(started, stopped, undone, failed)
