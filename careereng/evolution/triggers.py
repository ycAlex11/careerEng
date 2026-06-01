"""Evolution trigger scans."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from careereng.evolution.runs import create_evolution_run
from careereng.storage.jsonl import JSONLStore
from careereng.storage.site_store import SiteStore
from careereng.utils import ensure_dir, now_iso, read_json, write_json


SITE_WORKFLOW_CANDIDATE_ID = "site_workflow_compaction"
TARGET_COMPANY_CANDIDATE_ID = "target_company_intelligence_evolution"
ASSISTANT_ROUTER_MEMORY_CANDIDATE_ID = "assistant_router_memory_intake"
SITE_WORKFLOW_SCHEDULED_THRESHOLD = 10
SITE_WORKFLOW_PROBLEM_THRESHOLD = 2
SITE_WORKFLOW_PROBLEM_RECENT_LIMIT = 10
ASSISTANT_EXPLICIT_INTAKE_TOTAL_THRESHOLD = 50
ASSISTANT_EXPLICIT_INTAKE_NEW_THRESHOLD = 20
ASSISTANT_MEMORY_UNIT_TOTAL_THRESHOLD = 50
ASSISTANT_MEMORY_UNIT_NEW_THRESHOLD = 20
ASSISTANT_CORRECTION_NEW_THRESHOLD = 5
ASSISTANT_CODEX_IMPORTED_MEMORY_NEW_THRESHOLD = 10
JD_DEMAND_TOTAL_THRESHOLD = 30
JD_DEMAND_NEW_THRESHOLD = 15
REJECTION_TOTAL_THRESHOLD = 5
REJECTION_NEW_THRESHOLD = 3
FAST_REJECTION_THRESHOLD = 3
POSITIVE_PROGRESS_STATUSES = {
    "in_process",
    "application_in_review",
    "assessment",
    "interview",
    "recruiter_screen",
    "offer",
}
FEEDBACK_REVIEW_THRESHOLD = 10
LONG_PENDING_THRESHOLD = 5
LONG_PENDING_DAYS = 30
PENDING_STATUSES = {
    "active",
    "submitted",
    "application_received",
    "received",
    "in_review",
    "application_in_review",
}
TERMINAL_PHASE_EVENTS = {
    "browser.phase.done": "done",
    "browser.phase.blocked": "blocked",
    "browser.phase.failed": "failed",
}
PROBLEM_BROWSER_EVENT_TYPES = {
    "same_url_no_progress",
    "same_url_no_progress_tokens",
    "empty_extraction_loop",
    "retrieval_stop_recommended",
    "retrieval_enrichment_required",
}
PHASE_SECTION_BY_SLUG = {
    "session_preparation": "Session Preparation",
    "application_status_review": "Application Status Review",
    "channel_discovery": "Channel Discovery",
    "job_filtering": "Job Filtering",
    "job_retrieval": "Job Retrieval",
    "apply": "Apply",
}


class EvolutionTriggerError(ValueError):
    """Raised when trigger scanning cannot proceed."""


def scan_evolution_triggers(
    *,
    project_root: Path | str,
    workspace: Path | str,
    status: str = "active",
    create_runs: bool = True,
) -> dict[str, Any]:
    site_workflow = scan_site_workflow_triggers(
        project_root=project_root,
        workspace=workspace,
        status=status,
        create_runs=create_runs,
    )
    target_company = scan_target_company_intelligence_triggers(
        project_root=project_root,
        workspace=workspace,
        status=status,
        create_runs=create_runs,
    )
    assistant_memory = scan_assistant_router_memory_triggers(
        project_root=project_root,
        workspace=workspace,
        create_runs=create_runs,
    )
    return {
        "generated_at": now_iso(),
        "site_workflow": site_workflow,
        "target_company_intelligence": target_company,
        "assistant_router_memory_intake": assistant_memory,
        "triggered_count": (
            int(site_workflow.get("triggered_count") or 0)
            + int(target_company.get("triggered_count") or 0)
            + int(assistant_memory.get("triggered_count") or 0)
        ),
    }


def scan_site_workflow_triggers(
    *,
    project_root: Path | str,
    workspace: Path | str,
    status: str = "active",
    create_runs: bool = True,
) -> dict[str, Any]:
    root = Path(project_root)
    workspace_path = Path(workspace)
    site_store = SiteStore(workspace_path, project_root=root)
    sites = site_store.list_sites(status=status or None)
    state = _load_trigger_state(workspace_path)
    previous_site_state = state.setdefault("site_workflow", {})
    phase_stats = _collect_site_phase_stats(workspace=workspace_path, site_store=site_store, sites=sites)
    browser_problem_counts = _browser_problem_counts(workspace_path)

    triggered: list[dict[str, Any]] = []
    scanned_buckets = 0
    for key in sorted(phase_stats):
        scanned_buckets += 1
        stats = phase_stats[key]
        site_key = str(stats.get("site_key") or "")
        phase = str(stats.get("phase") or "")
        current_count = int(stats.get("phase_run_count") or 0)
        previous = previous_site_state.get(key) if isinstance(previous_site_state.get(key), dict) else {}
        last_evolved_count = int(previous.get("last_evolved_phase_run_count") or 0)
        reasons: list[str] = []
        trigger_type = ""
        if current_count - last_evolved_count >= SITE_WORKFLOW_SCHEDULED_THRESHOLD:
            trigger_type = "scheduled"
            reasons.append(
                f"{site_key}:{phase} reached {current_count - last_evolved_count} new terminal phase run(s) since last evolution."
            )

        problem_count = int(stats.get("recent_problem_terminal_count") or 0) + int(browser_problem_counts.get(key, 0))
        if problem_count >= SITE_WORKFLOW_PROBLEM_THRESHOLD:
            if not trigger_type:
                trigger_type = "problem_driven"
            reasons.append(f"{site_key}:{phase} has {problem_count} recent problem signal(s).")

        if not trigger_type:
            continue

        candidate = _trigger_candidate_row(
            workspace=workspace_path,
            site_store=site_store,
            site_key=site_key,
            phase=phase,
            trigger_type=trigger_type,
            stats=stats,
            last_evolved_count=last_evolved_count,
            reasons=reasons,
        )
        run_result = {}
        if create_runs:
            run_result = create_evolution_run(
                project_root=root,
                workspace=workspace_path,
                candidate_id=SITE_WORKFLOW_CANDIDATE_ID,
            )
            _attach_trigger_to_run(
                run_dir=Path(run_result["run_dir"]),
                trigger=candidate,
                site_key=site_key,
                phase=phase,
            )
            candidate["evolution_run_id"] = str(run_result.get("run_id") or "")
            candidate["run_dir"] = str(run_result.get("run_dir") or "")

        if create_runs:
            JSONLStore(workspace_path / "evolution" / "candidates" / "open.jsonl").append(candidate)
            previous_site_state[key] = {
                "site_key": site_key,
                "phase": phase,
                "phase_run_count": current_count,
                "last_evolved_phase_run_count": current_count,
                "last_evolution_run_id": str(candidate.get("evolution_run_id") or ""),
                "last_triggered_at": candidate["created_at"],
                "last_trigger_type": trigger_type,
                "last_reason": " ".join(reasons),
            }
        triggered.append(candidate)

    generated_at = now_iso()
    if create_runs:
        state["updated_at"] = generated_at
        write_json(_trigger_state_path(workspace_path), state)
    return {
        "generated_at": generated_at,
        "candidate_id": SITE_WORKFLOW_CANDIDATE_ID,
        "site_count": len(sites),
        "bucket_count": scanned_buckets,
        "triggered_count": len(triggered),
        "triggered": triggered,
        "state_path": str(_trigger_state_path(workspace_path)),
        "open_candidates_path": str(workspace_path / "evolution" / "candidates" / "open.jsonl"),
    }


def scan_target_company_intelligence_triggers(
    *,
    project_root: Path | str,
    workspace: Path | str,
    status: str = "active",
    create_runs: bool = True,
) -> dict[str, Any]:
    root = Path(project_root)
    workspace_path = Path(workspace)
    site_store = SiteStore(workspace_path, project_root=root)
    sites = site_store.list_sites(status=status or None)
    state = _load_trigger_state(workspace_path)
    previous_state = state.setdefault("target_company_intelligence", {})
    stats_by_site = _collect_target_company_stats(workspace=workspace_path, site_store=site_store, sites=sites)

    triggered: list[dict[str, Any]] = []
    scanned_buckets = 0
    for site_key in sorted(stats_by_site):
        stats = stats_by_site[site_key]
        for area in ("jd_demand", "rejection_pattern", "positive_progress", "feedback_behavior"):
            scanned_buckets += 1
            key = _company_bucket_key(site_key, area)
            previous = previous_state.get(key) if isinstance(previous_state.get(key), dict) else {}
            reasons = _target_company_reasons(area=area, stats=stats, previous=previous)
            if not reasons:
                continue
            candidate = _target_company_candidate_row(
                workspace=workspace_path,
                site_key=site_key,
                area=area,
                stats=stats,
                previous=previous,
                reasons=reasons,
            )
            if create_runs:
                run_result = create_evolution_run(
                    project_root=root,
                    workspace=workspace_path,
                    candidate_id=TARGET_COMPANY_CANDIDATE_ID,
                )
                _attach_trigger_to_run(
                    run_dir=Path(run_result["run_dir"]),
                    trigger=candidate,
                    site_key=site_key,
                    phase=area,
                )
                candidate["evolution_run_id"] = str(run_result.get("run_id") or "")
                candidate["run_dir"] = str(run_result.get("run_dir") or "")

            if create_runs:
                JSONLStore(workspace_path / "evolution" / "candidates" / "open.jsonl").append(candidate)
                previous_state[key] = _target_company_state_row(
                    site_key=site_key,
                    area=area,
                    stats=stats,
                    candidate=candidate,
                )
            triggered.append(candidate)

    generated_at = now_iso()
    if create_runs:
        state["updated_at"] = generated_at
        write_json(_trigger_state_path(workspace_path), state)
    return {
        "generated_at": generated_at,
        "candidate_id": TARGET_COMPANY_CANDIDATE_ID,
        "site_count": len(sites),
        "bucket_count": scanned_buckets,
        "triggered_count": len(triggered),
        "triggered": triggered,
        "state_path": str(_trigger_state_path(workspace_path)),
        "open_candidates_path": str(workspace_path / "evolution" / "candidates" / "open.jsonl"),
    }


def scan_assistant_router_memory_triggers(
    *,
    project_root: Path | str,
    workspace: Path | str,
    create_runs: bool = True,
) -> dict[str, Any]:
    root = Path(project_root)
    workspace_path = Path(workspace)
    state = _load_trigger_state(workspace_path)
    previous_state = state.setdefault("assistant_router_memory_intake", {})
    stats = _collect_assistant_router_memory_stats(workspace_path)
    reasons = _assistant_router_memory_reasons(stats=stats, previous=previous_state)

    triggered: list[dict[str, Any]] = []
    if reasons:
        candidate = _assistant_router_memory_candidate_row(
            workspace=workspace_path,
            stats=stats,
            previous=previous_state,
            reasons=reasons,
        )
        if create_runs:
            run_result = create_evolution_run(
                project_root=root,
                workspace=workspace_path,
                candidate_id=ASSISTANT_ROUTER_MEMORY_CANDIDATE_ID,
            )
            _attach_trigger_to_run(
                run_dir=Path(run_result["run_dir"]),
                trigger=candidate,
                site_key="assistant_bridge",
                phase="memory_intake",
            )
            candidate["evolution_run_id"] = str(run_result.get("run_id") or "")
            candidate["run_dir"] = str(run_result.get("run_dir") or "")

        if create_runs:
            JSONLStore(workspace_path / "evolution" / "candidates" / "open.jsonl").append(candidate)
            previous_state.update(_assistant_router_memory_state_row(stats=stats, candidate=candidate))
        triggered.append(candidate)

    generated_at = now_iso()
    if create_runs:
        state["updated_at"] = generated_at
        write_json(_trigger_state_path(workspace_path), state)
    return {
        "generated_at": generated_at,
        "candidate_id": ASSISTANT_ROUTER_MEMORY_CANDIDATE_ID,
        "bucket_count": 1,
        "triggered_count": len(triggered),
        "triggered": triggered,
        "stats": stats,
        "state_path": str(_trigger_state_path(workspace_path)),
        "open_candidates_path": str(workspace_path / "evolution" / "candidates" / "open.jsonl"),
    }


def _collect_site_phase_stats(*, workspace: Path, site_store: SiteStore, sites: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for site in sites:
        site_key = str(site.get("site_key") or "").strip()
        if not site_key:
            continue
        events_path = site_store.site_dir(site_key) / "events" / "all.jsonl"
        events = JSONLStore(events_path).read_all() if events_path.exists() else []
        phase_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in events:
            name = str(row.get("name") or "")
            status = TERMINAL_PHASE_EVENTS.get(name)
            if not status:
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            phase = str(payload.get("phase") or "").strip()
            if not phase:
                continue
            phase_rows[phase].append(
                {
                    "ts": str(row.get("ts") or ""),
                    "status": status,
                    "reason_tag": str(payload.get("reason_tag") or ""),
                    "summary": str(payload.get("summary") or ""),
                    "source_ref": _source_ref(workspace, events_path),
                }
            )
        for phase, phase_events in phase_rows.items():
            key = _bucket_key(site_key, phase)
            statuses = Counter(str(item.get("status") or "") for item in phase_events)
            recent = phase_events[-SITE_WORKFLOW_PROBLEM_RECENT_LIMIT:]
            recent_problem_terminal_count = sum(1 for item in recent if item.get("status") in {"blocked", "failed"})
            rows[key] = {
                "site_key": site_key,
                "phase": phase,
                "phase_run_count": len(phase_events),
                "status_counts": dict(sorted(statuses.items())),
                "recent_problem_terminal_count": recent_problem_terminal_count,
                "latest_phase_at": str(phase_events[-1].get("ts") or "") if phase_events else "",
                "evidence_refs": sorted({str(item.get("source_ref") or "") for item in recent if item.get("source_ref")}),
            }
    return rows


def _collect_target_company_stats(*, workspace: Path, site_store: SiteStore, sites: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    generated_at = now_iso()
    rows: dict[str, dict[str, Any]] = {}
    for site in sites:
        site_key = str(site.get("site_key") or "").strip()
        if not site_key:
            continue
        history_rows = [row for row in site_store.list_jobs(site_key) if isinstance(row, dict)]
        review_rows = _load_review_rows(workspace, site_key)
        rejected_rows = [row for row in history_rows if _normalized_status(row.get("application_review_status")) == "rejected"]
        fast_rejections = []
        for row in rejected_rows:
            days = _days_between(_best_application_start_date(row), row.get("application_review_checked_at"))
            if days is not None and days <= 7:
                fast_rejections.append(row)
        positive_rows = [
            row
            for row in history_rows
            if _row_has_positive_progress(row)
        ]
        long_pending_rows = [
            row
            for row in history_rows
            if _is_long_pending(row, generated_at=generated_at)
        ]
        rows[site_key] = {
            "site_key": site_key,
            "job_count": len(history_rows),
            "rejected_count": len(rejected_rows),
            "fast_rejection_count": len(fast_rejections),
            "positive_progress_count": len(positive_rows),
            "positive_progress_keys": sorted(_row_identity(row) for row in positive_rows if _row_identity(row)),
            "review_count": len(review_rows),
            "long_pending_count": len(long_pending_rows),
            "latest_job_at": _latest_text(history_rows, ("last_seen_at", "first_seen_at", "application_updated_at")),
            "latest_review_at": _latest_text(review_rows, ("checked_at", "ts")),
            "evidence_refs": [
                _source_ref(workspace, site_store.site_dir(site_key) / "jobs" / "history_jobs.json"),
                _source_ref(workspace, site_store.site_dir(site_key) / "applications" / "reviews"),
            ],
        }
    return rows


def _target_company_reasons(*, area: str, stats: dict[str, Any], previous: dict[str, Any]) -> list[str]:
    site_key = str(stats.get("site_key") or "")
    reasons: list[str] = []
    if area == "jd_demand":
        job_count = int(stats.get("job_count") or 0)
        last_count = int(previous.get("last_evolved_job_count") or 0)
        if job_count >= JD_DEMAND_TOTAL_THRESHOLD and last_count == 0:
            reasons.append(f"{site_key} has {job_count} local job records.")
        elif job_count - last_count >= JD_DEMAND_NEW_THRESHOLD:
            reasons.append(f"{site_key} has {job_count - last_count} new job records since last intelligence evolution.")
    elif area == "rejection_pattern":
        rejected_count = int(stats.get("rejected_count") or 0)
        fast_count = int(stats.get("fast_rejection_count") or 0)
        last_rejected = int(previous.get("last_evolved_rejected_count") or 0)
        if rejected_count >= REJECTION_TOTAL_THRESHOLD and last_rejected == 0:
            reasons.append(f"{site_key} has {rejected_count} rejected applications.")
        elif rejected_count - last_rejected >= REJECTION_NEW_THRESHOLD:
            reasons.append(f"{site_key} has {rejected_count - last_rejected} new rejected applications since last intelligence evolution.")
        if fast_count >= FAST_REJECTION_THRESHOLD and fast_count > int(previous.get("last_evolved_fast_rejection_count") or 0):
            reasons.append(f"{site_key} has {fast_count} fast rejection signal(s).")
    elif area == "positive_progress":
        keys = set(str(item) for item in stats.get("positive_progress_keys") or [] if str(item))
        seen = set(str(item) for item in previous.get("last_seen_positive_progress_keys") or [] if str(item))
        new_keys = sorted(keys - seen)
        if new_keys:
            reasons.append(f"{site_key} has {len(new_keys)} new positive-progress application signal(s).")
    elif area == "feedback_behavior":
        review_count = int(stats.get("review_count") or 0)
        pending_count = int(stats.get("long_pending_count") or 0)
        last_review = int(previous.get("last_evolved_review_count") or 0)
        last_pending = int(previous.get("last_evolved_long_pending_count") or 0)
        if review_count >= FEEDBACK_REVIEW_THRESHOLD and last_review == 0:
            reasons.append(f"{site_key} has {review_count} application review records.")
        elif review_count - last_review >= FEEDBACK_REVIEW_THRESHOLD:
            reasons.append(f"{site_key} has {review_count - last_review} new application review records since last feedback evolution.")
        if pending_count >= LONG_PENDING_THRESHOLD and pending_count > last_pending:
            reasons.append(f"{site_key} has {pending_count} long-pending application(s).")
    return reasons


def _target_company_candidate_row(
    *,
    workspace: Path,
    site_key: str,
    area: str,
    stats: dict[str, Any],
    previous: dict[str, Any],
    reasons: list[str],
) -> dict[str, Any]:
    priority = "high" if area in {"positive_progress", "rejection_pattern"} else "medium"
    return {
        "candidate_id": TARGET_COMPANY_CANDIDATE_ID,
        "area": area,
        "target_ref": f"target_company:{site_key}#{area}",
        "priority": priority,
        "status": "open",
        "created_at": now_iso(),
        "site_key": site_key,
        "trigger_type": area,
        "job_count": int(stats.get("job_count") or 0),
        "rejected_count": int(stats.get("rejected_count") or 0),
        "fast_rejection_count": int(stats.get("fast_rejection_count") or 0),
        "positive_progress_count": int(stats.get("positive_progress_count") or 0),
        "review_count": int(stats.get("review_count") or 0),
        "long_pending_count": int(stats.get("long_pending_count") or 0),
        "previous_counts": {
            "job_count": int(previous.get("last_evolved_job_count") or 0),
            "rejected_count": int(previous.get("last_evolved_rejected_count") or 0),
            "review_count": int(previous.get("last_evolved_review_count") or 0),
            "long_pending_count": int(previous.get("last_evolved_long_pending_count") or 0),
        },
        "reason": " ".join(reasons).strip(),
        "summary": f"{site_key} is ready for target-company intelligence evolution: {area}.",
        "suggested_change": _target_company_suggested_change(site_key=site_key, area=area),
        "evidence_refs": stats.get("evidence_refs") if isinstance(stats.get("evidence_refs"), list) else [],
        "state_ref": str(_trigger_state_path(workspace)),
    }


def _target_company_suggested_change(*, site_key: str, area: str) -> str:
    if area == "jd_demand":
        return f"Analyze `{site_key}` retrieved jobs to summarize company skill demand, role clusters, user gaps, and preparation direction."
    if area == "rejection_pattern":
        return f"Analyze `{site_key}` rejected jobs to find lower-priority role clusters and evidence-backed user gaps."
    if area == "positive_progress":
        return f"Analyze `{site_key}` positive-progress applications to identify promising role clusters and interview preparation targets."
    if area == "feedback_behavior":
        return f"Analyze `{site_key}` review records and long-pending applications to summarize company feedback behavior and status timing."
    return f"Analyze `{site_key}` target-company intelligence."


def _target_company_state_row(*, site_key: str, area: str, stats: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "site_key": site_key,
        "intelligence_area": area,
        "last_evolved_job_count": int(stats.get("job_count") or 0),
        "last_evolved_rejected_count": int(stats.get("rejected_count") or 0),
        "last_evolved_fast_rejection_count": int(stats.get("fast_rejection_count") or 0),
        "last_evolved_review_count": int(stats.get("review_count") or 0),
        "last_evolved_long_pending_count": int(stats.get("long_pending_count") or 0),
        "last_seen_positive_progress_keys": stats.get("positive_progress_keys") if isinstance(stats.get("positive_progress_keys"), list) else [],
        "last_triggered_at": candidate["created_at"],
        "last_evolution_run_id": str(candidate.get("evolution_run_id") or ""),
        "last_reason": str(candidate.get("reason") or ""),
    }


def _collect_assistant_router_memory_stats(workspace: Path) -> dict[str, Any]:
    intake_path = workspace / "assistant_bridge" / "intake_events.jsonl"
    routing_examples_path = workspace / "assistant_bridge" / "routing_examples.jsonl"
    corrections_path = workspace / "assistant_bridge" / "correction_events.jsonl"
    actions_path = workspace / "assistant_bridge" / "action_events.jsonl"
    memory_units_path = workspace / "memory" / "memory_units.jsonl"

    intake_rows = JSONLStore(intake_path).read_all() if intake_path.exists() else []
    explicit_rows = [
        row
        for row in intake_rows
        if bool(row.get("explicit_trigger")) or str(row.get("trigger_mode") or "") == "explicit"
    ]
    memory_rows = JSONLStore(memory_units_path).read_all() if memory_units_path.exists() else []
    imported_memory_rows = [row for row in memory_rows if _is_codex_imported_memory(row)]
    correction_rows = JSONLStore(corrections_path).read_all() if corrections_path.exists() else []
    routing_rows = JSONLStore(routing_examples_path).read_all() if routing_examples_path.exists() else []
    action_rows = JSONLStore(actions_path).read_all() if actions_path.exists() else []

    return {
        "explicit_intake_count": len(explicit_rows),
        "intake_count": len(intake_rows),
        "memory_unit_count": len(memory_rows),
        "codex_imported_memory_count": len(imported_memory_rows),
        "correction_count": len(correction_rows),
        "routing_example_count": len(routing_rows),
        "action_event_count": len(action_rows),
        "latest_intake_at": _latest_text(intake_rows, ("created_at",)),
        "latest_memory_at": _latest_text(memory_rows, ("created_at", "updated_at")),
        "latest_correction_at": _latest_text(correction_rows, ("created_at",)),
        "evidence_refs": [
            _source_ref(workspace, intake_path),
            _source_ref(workspace, routing_examples_path),
            _source_ref(workspace, corrections_path),
            _source_ref(workspace, actions_path),
            _source_ref(workspace, memory_units_path),
        ],
    }


def _assistant_router_memory_reasons(*, stats: dict[str, Any], previous: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    explicit_count = int(stats.get("explicit_intake_count") or 0)
    memory_count = int(stats.get("memory_unit_count") or 0)
    correction_count = int(stats.get("correction_count") or 0)
    imported_count = int(stats.get("codex_imported_memory_count") or 0)

    last_explicit = int(previous.get("last_evolved_explicit_intake_count") or 0)
    last_memory = int(previous.get("last_evolved_memory_unit_count") or 0)
    last_correction = int(previous.get("last_evolved_correction_count") or 0)
    last_imported = int(previous.get("last_evolved_codex_imported_memory_count") or 0)

    if explicit_count >= ASSISTANT_EXPLICIT_INTAKE_TOTAL_THRESHOLD and last_explicit == 0:
        reasons.append(f"assistant bridge has {explicit_count} explicit @career intake event(s).")
    elif explicit_count - last_explicit >= ASSISTANT_EXPLICIT_INTAKE_NEW_THRESHOLD:
        reasons.append(f"assistant bridge has {explicit_count - last_explicit} new explicit @career intake event(s) since last evolution.")

    if memory_count >= ASSISTANT_MEMORY_UNIT_TOTAL_THRESHOLD and last_memory == 0:
        reasons.append(f"career memory has {memory_count} unified memory unit(s).")
    elif memory_count - last_memory >= ASSISTANT_MEMORY_UNIT_NEW_THRESHOLD:
        reasons.append(f"career memory has {memory_count - last_memory} new memory unit(s) since last evolution.")

    if correction_count - last_correction >= ASSISTANT_CORRECTION_NEW_THRESHOLD:
        reasons.append(f"assistant bridge has {correction_count - last_correction} new correction event(s) since last evolution.")

    if imported_count - last_imported >= ASSISTANT_CODEX_IMPORTED_MEMORY_NEW_THRESHOLD:
        reasons.append(f"career memory has {imported_count - last_imported} new Codex-imported memory unit(s) since last evolution.")

    return reasons


def _assistant_router_memory_candidate_row(
    *,
    workspace: Path,
    stats: dict[str, Any],
    previous: dict[str, Any],
    reasons: list[str],
) -> dict[str, Any]:
    correction_delta = int(stats.get("correction_count") or 0) - int(previous.get("last_evolved_correction_count") or 0)
    priority = "high" if correction_delta >= ASSISTANT_CORRECTION_NEW_THRESHOLD else "medium"
    return {
        "candidate_id": ASSISTANT_ROUTER_MEMORY_CANDIDATE_ID,
        "area": "assistant_router_memory_intake",
        "target_ref": "careereng/integrations/assistant_bridge/#memory-intake",
        "priority": priority,
        "status": "open",
        "created_at": now_iso(),
        "trigger_type": "assistant_memory_intake",
        "explicit_intake_count": int(stats.get("explicit_intake_count") or 0),
        "memory_unit_count": int(stats.get("memory_unit_count") or 0),
        "correction_count": int(stats.get("correction_count") or 0),
        "codex_imported_memory_count": int(stats.get("codex_imported_memory_count") or 0),
        "routing_example_count": int(stats.get("routing_example_count") or 0),
        "previous_counts": {
            "explicit_intake_count": int(previous.get("last_evolved_explicit_intake_count") or 0),
            "memory_unit_count": int(previous.get("last_evolved_memory_unit_count") or 0),
            "correction_count": int(previous.get("last_evolved_correction_count") or 0),
            "codex_imported_memory_count": int(previous.get("last_evolved_codex_imported_memory_count") or 0),
        },
        "reason": " ".join(reasons).strip(),
        "summary": "Assistant router and career-memory intake are ready for evidence-backed evolution.",
        "suggested_change": (
            "Review @career intake, promoted memory units, Codex-imported memory, and corrections to improve "
            "routing examples, curation guidance, confirmation policy, and suppress rules. Do not enable automatic "
            "implicit saves without user confirmation."
        ),
        "evidence_refs": stats.get("evidence_refs") if isinstance(stats.get("evidence_refs"), list) else [],
        "state_ref": str(_trigger_state_path(workspace)),
    }


def _assistant_router_memory_state_row(*, stats: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "last_evolved_explicit_intake_count": int(stats.get("explicit_intake_count") or 0),
        "last_evolved_memory_unit_count": int(stats.get("memory_unit_count") or 0),
        "last_evolved_correction_count": int(stats.get("correction_count") or 0),
        "last_evolved_codex_imported_memory_count": int(stats.get("codex_imported_memory_count") or 0),
        "last_triggered_at": candidate["created_at"],
        "last_evolution_run_id": str(candidate.get("evolution_run_id") or ""),
        "last_reason": str(candidate.get("reason") or ""),
    }


def _is_codex_imported_memory(row: dict[str, Any]) -> bool:
    source_thread_id = str(row.get("source_thread_id") or "").strip()
    if source_thread_id:
        return True
    source_path = str(row.get("source_path") or "").strip()
    raw_signal_paths = {
        "memory/profile_signals.jsonl",
        "memory/intent_signals.jsonl",
        "memory/application_feedback_signals.jsonl",
        "interviews/events.jsonl",
        "assistant_bridge/correction_events.jsonl",
    }
    return bool(source_path and source_path not in raw_signal_paths)


def _browser_problem_counts(workspace: Path) -> dict[str, int]:
    path = workspace / "evolution" / "browser_control" / "phase_events.jsonl"
    if not path.exists():
        return {}
    recent_by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in JSONLStore(path).read_all():
        if str(row.get("event_type") or "") not in PROBLEM_BROWSER_EVENT_TYPES:
            continue
        site_key = str(row.get("site_key") or "").strip()
        phase = str(row.get("phase") or "").strip()
        if not site_key or not phase:
            continue
        bucket = _bucket_key(site_key, phase)
        recent_by_bucket[bucket].append(row)
    return {
        bucket: len(rows[-SITE_WORKFLOW_PROBLEM_RECENT_LIMIT:])
        for bucket, rows in recent_by_bucket.items()
    }


def _trigger_candidate_row(
    *,
    workspace: Path,
    site_store: SiteStore,
    site_key: str,
    phase: str,
    trigger_type: str,
    stats: dict[str, Any],
    last_evolved_count: int,
    reasons: list[str],
) -> dict[str, Any]:
    phase_section = PHASE_SECTION_BY_SLUG.get(phase, phase.replace("_", " ").title())
    skill_path = site_store.site_skill_path(site_key)
    target_ref = f"{_relative_or_str(skill_path, site_store.project_root)}#{phase_section}"
    return {
        "candidate_id": SITE_WORKFLOW_CANDIDATE_ID,
        "area": "site_workflow",
        "target_ref": target_ref,
        "priority": "medium" if trigger_type == "scheduled" else "high",
        "status": "open",
        "created_at": now_iso(),
        "site_key": site_key,
        "phase": phase,
        "trigger_type": trigger_type,
        "phase_run_count": int(stats.get("phase_run_count") or 0),
        "last_evolved_phase_run_count": int(last_evolved_count or 0),
        "status_counts": stats.get("status_counts") if isinstance(stats.get("status_counts"), dict) else {},
        "latest_phase_at": str(stats.get("latest_phase_at") or ""),
        "reason": " ".join(reasons).strip(),
        "summary": f"{site_key} {phase} workflow is ready for {trigger_type} evolution.",
        "suggested_change": (
            f"Review and compact the `{phase_section}` section for `{site_key}` using recent site workflow evidence."
        ),
        "evidence_refs": stats.get("evidence_refs") if isinstance(stats.get("evidence_refs"), list) else [],
        "state_ref": str(_trigger_state_path(workspace)),
    }


def _attach_trigger_to_run(*, run_dir: Path, trigger: dict[str, Any], site_key: str, phase: str) -> None:
    trigger_path = run_dir / "trigger.json"
    write_json(trigger_path, trigger)
    run_path = run_dir / "run.json"
    run_payload = read_json(run_path)
    if run_payload:
        run_payload["trigger"] = trigger
        outputs = run_payload.setdefault("outputs", {})
        outputs["trigger"] = str(trigger_path)
        candidate = run_payload.get("candidate") if isinstance(run_payload.get("candidate"), dict) else {}
        if candidate:
            candidate["target_ref"] = str(trigger.get("target_ref") or candidate.get("target_ref") or "")
            run_payload["candidate"] = candidate
        write_json(run_path, run_payload)
    evidence_pack = run_dir / "evidence_pack.md"
    if evidence_pack.exists():
        text = evidence_pack.read_text(encoding="utf-8").rstrip()
        text += (
            "\n\n## Trigger Scope\n\n"
            f"- Site: `{site_key}`\n"
            f"- Phase/Area: `{phase}`\n"
            f"- Trigger Type: `{trigger.get('trigger_type')}`\n"
            f"- Phase Run Count: `{trigger.get('phase_run_count')}`\n"
            f"- Last Evolved Phase Run Count: `{trigger.get('last_evolved_phase_run_count')}`\n"
            f"- Reason: {trigger.get('reason')}\n"
        )
        evidence_pack.write_text(text.rstrip() + "\n", encoding="utf-8")


def _load_trigger_state(workspace: Path) -> dict[str, Any]:
    path = _trigger_state_path(workspace)
    payload = read_json(path)
    if not payload:
        return {
            "version": 1,
            "site_workflow": {},
            "target_company_intelligence": {},
            "assistant_router_memory_intake": {},
            "updated_at": "",
        }
    payload.setdefault("version", 1)
    payload.setdefault("site_workflow", {})
    payload.setdefault("target_company_intelligence", {})
    payload.setdefault("assistant_router_memory_intake", {})
    return payload


def _trigger_state_path(workspace: Path) -> Path:
    ensure_dir(workspace / "evolution" / "triggers")
    return workspace / "evolution" / "triggers" / "site_workflow_state.json"


def _bucket_key(site_key: str, phase: str) -> str:
    return f"{site_key.strip()}::{phase.strip()}"


def _company_bucket_key(site_key: str, area: str) -> str:
    return f"{site_key.strip()}::{area.strip()}"


def _load_review_rows(workspace: Path, site_key: str) -> list[dict[str, Any]]:
    review_dir = workspace / "sites" / site_key / "applications" / "reviews"
    rows: list[dict[str, Any]] = []
    if not review_dir.exists():
        return rows
    for path in sorted(review_dir.glob("*.jsonl")):
        rows.extend(JSONLStore(path).read_all())
    return [row for row in rows if isinstance(row, dict)]


def _normalized_status(value: Any) -> str:
    return "_".join(str(value or "").strip().lower().replace("-", "_").split())


def _normalized_stage(value: Any) -> str:
    return "_".join(str(value or "").strip().lower().replace("-", "_").split())


def _row_has_positive_progress(row: dict[str, Any]) -> bool:
    values = {
        _normalized_status(row.get("application_review_status")),
        _normalized_status(row.get("application_status")),
        _normalized_stage(row.get("application_review_stage")),
        _normalized_status(row.get("application_review_status_raw")),
    }
    return bool(values & POSITIVE_PROGRESS_STATUSES)


def _is_long_pending(row: dict[str, Any], *, generated_at: str) -> bool:
    status_values = {
        _normalized_status(row.get("application_review_status")),
        _normalized_status(row.get("application_status")),
        _normalized_stage(row.get("application_review_stage")),
    }
    if not (status_values & PENDING_STATUSES):
        return False
    days = _days_between(_best_application_start_date(row), generated_at)
    return days is not None and days >= LONG_PENDING_DAYS


def _best_application_start_date(row: dict[str, Any]) -> str:
    for field in ("last_submitted_at", "application_updated_at", "first_seen_at", "application_review_checked_at", "checked_at", "ts"):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def _days_between(start: Any, end: Any) -> int | None:
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if start_dt is None or end_dt is None:
        return None
    try:
        return max(0, (end_dt.date() - start_dt.date()).days)
    except Exception:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text, text[:10]]
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _row_identity(row: dict[str, Any]) -> str:
    for field in ("job_id", "canonical_job_id", "site_job_id", "url", "title"):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def _latest_text(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> str:
    latest = ""
    for row in rows:
        for field in fields:
            value = str(row.get(field) or "").strip()
            if value and value > latest:
                latest = value
    return latest


def _relative_or_str(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _source_ref(workspace: Path, path: Path) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)
