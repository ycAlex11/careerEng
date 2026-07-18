"""CLI presentation helpers for observing an already-running job batch."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from careereng.career.applications.job_store import JobStore
from careereng.platform.persistence import JSONLStore
from careereng.utils import safe_file_stem


TERMINAL_BATCH_STATUSES = frozenset(
    {"completed", "partial_completed", "failed", "cancelled", "waiting_solution", "waiting_user"}
)
_PHASE_EVENT_LABELS = {
    "browser.phase.done": "done",
    "browser.phase.blocked": "blocked",
    "browser.phase.failed": "failed",
}


def new_batch_baseline(*, workspace: Path, session_id: str) -> set[str]:
    """Return the batches that existed before a CLI command starts work."""
    return {
        str(row.get("batch_id") or "")
        for row in JobStore(workspace).list_batches(session_id=session_id, include_terminal=True)
        if str(row.get("batch_id") or "")
    }


def emit_new_phase_events(
    *,
    workspace: Path,
    session_id: str,
    baseline_batch_ids: set[str],
    state: dict[str, Any],
    emit: Callable[[str], None],
) -> int:
    """Emit unseen phase events for one batch without interpreting business state."""
    store = JobStore(workspace)
    batch_id = str(state.get("batch_id") or "")
    if not batch_id:
        for row in store.list_batches(session_id=session_id, include_terminal=True):
            candidate = str(row.get("batch_id") or "")
            if candidate and candidate not in baseline_batch_ids:
                state["batch_id"] = candidate
                state["turn_id"] = str(row.get("turn_id") or "")
                batch_id = candidate
                break
    if not batch_id:
        return 0
    batch = store.load_batch(batch_id)
    turn_id = str(state.get("turn_id") or batch.get("turn_id") or "")
    if turn_id:
        state["turn_id"] = turn_id
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    seen = state.setdefault("seen_phase_events", set())
    pending: list[tuple[str, str, tuple[str, str, str, str, str]]] = []
    for site_key in sorted(sites):
        event_path = workspace / "sites" / safe_file_stem(site_key) / "events" / "all.jsonl"
        if not event_path.exists():
            continue
        for event in JSONLStore(event_path).read_all():
            if not isinstance(event, dict) or str(event.get("name") or "") not in _PHASE_EVENT_LABELS:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if turn_id and str(payload.get("turn_id") or "") != turn_id:
                continue
            name = str(event.get("name") or "")
            key = (site_key, str(event.get("ts") or ""), name, str(payload.get("phase") or ""), str(payload.get("summary") or ""))
            if key not in seen:
                phase = str(payload.get("phase") or "").strip()
                line = f"{site_key} {_PHASE_EVENT_LABELS[name]}" + (f" {phase}" if phase else "")
                pending.append((str(event.get("ts") or ""), line, key))
    for _ts, line, key in sorted(pending):
        seen.add(key)
        emit(line)
    return len(pending)


def dispatch_with_phase_progress(
    *,
    dispatch: Callable[[], str],
    workspace: Path,
    session_id: str,
    emit: Callable[[str], None],
) -> str:
    """Run a generic manager dispatch while streaming any created phase events."""
    baseline = new_batch_baseline(workspace=workspace, session_id=session_id)
    state: dict[str, Any] = {}
    result: dict[str, object] = {"reply": "", "error": None}

    def worker() -> None:
        try:
            result["reply"] = dispatch()
        except BaseException as exc:  # pragma: no cover - CLI transport boundary
            result["error"] = exc

    thread = threading.Thread(target=worker, name="careereng-cli-run", daemon=True)
    thread.start()
    while thread.is_alive():
        emit_new_phase_events(
            workspace=workspace,
            session_id=session_id,
            baseline_batch_ids=baseline,
            state=state,
            emit=emit,
        )
        thread.join(timeout=0.75)
    emit_new_phase_events(
        workspace=workspace,
        session_id=session_id,
        baseline_batch_ids=baseline,
        state=state,
        emit=emit,
    )
    error = result["error"]
    if isinstance(error, BaseException):
        raise error
    return str(result["reply"] or "")


def format_batch_summary(batch: dict[str, Any], *, workspace: Path) -> str:
    """Render persisted batch state for the CLI without changing it."""
    lines = [
        f"batch={batch.get('batch_id') or ''} status={batch.get('status') or 'unknown'} "
        f"operation={batch.get('operation') or 'job_search'}"
    ]
    report_date = str(batch.get("created_at") or "")[:10]
    batch_id = str(batch.get("batch_id") or "")
    if report_date and batch_id:
        report_dir = workspace / "reports" / "jobs" / report_date
        lines.extend([f"report={report_dir / f'{batch_id}.md'}", f"final_report={report_dir / 'final.md'}"])
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    for site_key in sorted(sites):
        site = sites[site_key]
        if not isinstance(site, dict):
            continue
        retrieve = site.get("retrieve") if isinstance(site.get("retrieve"), dict) else {}
        apply = site.get("apply") if isinstance(site.get("apply"), dict) else {}
        line = (
            f"- {site.get('site_name') or site_key} [{site_key}] status={site.get('status') or 'unknown'} "
            f"phase={site.get('current_phase') or ''} retrieve={retrieve.get('status') or 'skipped'} "
            f"apply={apply.get('status') or 'skipped'}"
        )
        reason = str(site.get("reason_tag") or apply.get("reason_tag") or retrieve.get("reason_tag") or "")
        lines.append(f"{line} reason={reason}" if reason else line)
    return "\n".join(lines)
