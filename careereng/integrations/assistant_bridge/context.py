"""Build a compact assistant-readable CareerEng context pack."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from careereng.integrations.assistant_bridge.intake_state import load_recent_intake_state
from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, now_iso


DEFAULT_RECENT_LIMIT = 8


def build_assistant_context_pack(
    *,
    project_root: Path | str,
    workspace: Path | str,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
) -> dict[str, Any]:
    root = Path(project_root)
    workspace_path = Path(workspace)
    limit = max(1, int(recent_limit or DEFAULT_RECENT_LIMIT))
    context_dir = ensure_dir(workspace_path / "assistant_bridge" / "context")
    output_path = context_dir / "latest.md"
    generated_at = now_iso()
    text = "\n".join(
        [
            "# CareerEng Assistant Context",
            "",
            f"- Generated At: `{generated_at}`",
            f"- Workspace: `{workspace_path}`",
            "",
            "## How Codex Should Use This",
            "",
            "- Read this file before handling `@career`, memory, evolution, recent-conversation summaries, action cards, or CareerEng status questions.",
            "- Inspect local memory and lessons before importing recent conversation candidates.",
            "- Do not create new memory categories for one-off observations; use the existing assistant bridge and career memory categories first.",
            "- Treat reports and summaries as views; durable state lives in workspace JSON/JSONL stores.",
            "",
            _taskboard_section(workspace_path),
            _memory_section(workspace_path, limit),
            _lessons_section(workspace_path, limit),
            _open_candidates_section(workspace_path, limit),
            _action_cards_section(workspace_path, limit),
            _assistant_bridge_section(workspace_path, limit),
            _recent_intake_state_section(workspace_path),
            _candidate_files_section(workspace_path, limit),
            _application_summary_section(workspace_path),
            _metrics_section(workspace_path, limit),
            _reports_section(workspace_path, limit),
            _git_section(root),
            "## Recent-N Conversation Intake Rule",
            "",
            "- First read this context pack.",
            "- Check existing `workspace/memory/memory_units.jsonl` and `workspace/evolution/browser_control/lessons.jsonl`.",
            "- Import only missing, evidence-backed content.",
            "- Preserve the requested window size with `--limit N` when using `assistant import-recent`.",
            "- Use existing categories: `profile_resume_signal`, `career_intent_strategy`, `application_feedback`, `correction`, `interview_record`, `evolution_lesson`.",
        ]
    )
    output_path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return {
        "generated_at": generated_at,
        "path": str(output_path),
        "workspace": str(workspace_path),
    }


def _taskboard_section(workspace: Path) -> str:
    path = workspace / "taskboard" / "current.md"
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return "## Current Taskboard\n\nNo current taskboard found.\n"
    return "## Current Taskboard\n\n" + _read_limited(path, max_lines=120)


def _memory_section(workspace: Path, limit: int) -> str:
    rows = _read_jsonl(workspace / "memory" / "memory_units.jsonl")[-limit:]
    return _rows_section(
        "Career Memory Units",
        rows,
        fields=("memory_id", "category", "status", "summary", "tags"),
        empty="No memory units found.",
    )


def _lessons_section(workspace: Path, limit: int) -> str:
    rows = _read_jsonl(workspace / "evolution" / "browser_control" / "lessons.jsonl")[-limit:]
    return _rows_section(
        "Accepted Evolution Lessons",
        rows,
        fields=("lesson_id", "scope", "site_key", "phase", "summary", "lesson", "recommendation"),
        empty="No browser-control lessons found.",
    )


def _open_candidates_section(workspace: Path, limit: int) -> str:
    rows = [
        row
        for row in _read_jsonl(workspace / "evolution" / "candidates" / "open.jsonl")
        if str(row.get("status") or "open") == "open"
    ][-limit:]
    return _rows_section(
        "Open Evolution Candidates",
        rows,
        fields=("candidate_id", "area", "site_key", "priority", "summary", "reason"),
        empty="No open evolution candidates found.",
    )


def _action_cards_section(workspace: Path, limit: int) -> str:
    rows = [
        row
        for row in _read_jsonl(workspace / "action_cards" / "index.jsonl")
        if str(row.get("status") or "") == "open"
    ][-limit:]
    return _rows_section(
        "Open Action Cards",
        rows,
        fields=("card_id", "card_type", "priority", "title", "goal", "reason"),
        empty="No open action cards found.",
    )


def _assistant_bridge_section(workspace: Path, limit: int) -> str:
    bridge = workspace / "assistant_bridge"
    intake_count = len(_read_jsonl(bridge / "intake_events.jsonl"))
    correction_rows = _read_jsonl(bridge / "correction_events.jsonl")
    routing_count = len(_read_jsonl(bridge / "routing_examples.jsonl"))
    action_count = len(_read_jsonl(bridge / "action_events.jsonl"))
    thread_state = _read_json(bridge / "thread_state.json")
    active_threads = 0
    threads = thread_state.get("threads") if isinstance(thread_state.get("threads"), dict) else {}
    for row in threads.values():
        if isinstance(row, dict) and row.get("active"):
            active_threads += 1
    lines = [
        "## Assistant Bridge State",
        "",
        f"- Intake events: `{intake_count}`",
        f"- Routing examples: `{routing_count}`",
        f"- Action events: `{action_count}`",
        f"- Corrections: `{len(correction_rows)}`",
        f"- Active thread scopes: `{active_threads}`",
    ]
    if correction_rows:
        lines.extend(["", "Recent corrections:"])
        lines.extend(_format_rows(correction_rows[-limit:], fields=("correction_id", "category", "summary", "reason")))
    return "\n".join(lines) + "\n"


def _recent_intake_state_section(workspace: Path) -> str:
    state = load_recent_intake_state(workspace)
    lines = ["## Recent Conversation Intake State", ""]
    if not state:
        lines.append("No recent conversation intake state found.")
        return "\n".join(lines) + "\n"
    fields = (
        "last_imported_at",
        "last_source_limit",
        "last_source_thread",
        "last_source_client",
        "last_candidate_file",
        "last_read_count",
        "last_created_memory_count",
        "last_created_lesson_count",
        "last_created_evolution_evidence_count",
        "last_skipped_existing",
        "last_context_refreshed_at",
        "last_context_path",
    )
    for field in fields:
        value = state.get(field)
        if value in (None, "", [], {}):
            continue
        lines.append(f"- {field}: `{_compact_value(value)}`")
    return "\n".join(lines) + "\n"


def _candidate_files_section(workspace: Path, limit: int) -> str:
    memory_dir = workspace / "memory"
    files = sorted(memory_dir.glob("codex_recent_*candidate*.jsonl"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    lines = ["## Recent Codex Candidate Files", ""]
    if not files:
        lines.append("No Codex candidate files found.")
        return "\n".join(lines) + "\n"
    for path in files[:limit]:
        lines.append(f"- `{path.relative_to(workspace)}` rows=`{len(_read_jsonl(path))}`")
    return "\n".join(lines) + "\n"


def _application_summary_section(workspace: Path) -> str:
    path = workspace / "application_summary" / "application_summary.json"
    payload = _read_json(path)
    lines = ["## Application Summary", ""]
    if not payload:
        lines.append("No application summary found.")
        return "\n".join(lines) + "\n"
    lines.append(f"- Source: `{path.relative_to(workspace)}`")
    for key in ("generated_at", "since", "total_applications", "submitted_count", "rejected_count", "unmatched_count", "legacy_unmatched_reviews"):
        if key in payload:
            lines.append(f"- {key}: `{payload.get(key)}`")
    return "\n".join(lines) + "\n"


def _metrics_section(workspace: Path, limit: int) -> str:
    usage_rows = _read_jsonl(workspace / "metrics" / "llm_usage.jsonl")
    summaries = sorted((workspace / "metrics" / "summaries").glob("*.json"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    lines = ["## Metrics", ""]
    lines.append(f"- LLM usage rows: `{len(usage_rows)}`")
    if usage_rows:
        lines.append("Recent usage:")
        lines.extend(_format_rows(usage_rows[-min(limit, 5) :], fields=("created_at", "model", "provider", "prompt_tokens", "completion_tokens", "total_tokens")))
    if summaries:
        lines.append("Recent metric summaries:")
        for path in summaries[: min(limit, 5)]:
            lines.append(f"- `{path.relative_to(workspace)}`")
    return "\n".join(lines) + "\n"


def _reports_section(workspace: Path, limit: int) -> str:
    reports_root = workspace / "reports" / "jobs"
    files = sorted(reports_root.glob("**/*.md"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    lines = ["## Recent Reports", ""]
    if not files:
        lines.append("No job reports found.")
        return "\n".join(lines) + "\n"
    for path in files[:limit]:
        lines.append(f"- `{path.relative_to(workspace)}`")
    return "\n".join(lines) + "\n"


def _git_section(project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=project_root,
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
        status = result.stdout.strip()
    except Exception as exc:
        status = f"Unable to read git status: {exc}"
    lines = ["## Git Dirty Files", ""]
    if status:
        lines.append("```text")
        lines.append(status)
        lines.append("```")
    else:
        lines.append("Working tree clean.")
    return "\n".join(lines) + "\n"


def _rows_section(title: str, rows: list[dict[str, Any]], *, fields: tuple[str, ...], empty: str) -> str:
    lines = [f"## {title}", ""]
    if not rows:
        lines.append(empty)
    else:
        lines.extend(_format_rows(rows, fields=fields))
    return "\n".join(lines) + "\n"


def _format_rows(rows: list[dict[str, Any]], *, fields: tuple[str, ...]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        chunks = []
        for field in fields:
            value = row.get(field)
            if value in (None, "", [], {}):
                continue
            chunks.append(f"{field}={_compact_value(value)}")
        lines.append("- " + ("; ".join(chunks) if chunks else json.dumps(row, ensure_ascii=False, sort_keys=True)[:300]))
    return lines


def _compact_value(value: Any, *, max_chars: int = 240) -> str:
    if isinstance(value, (list, tuple)):
        text = ", ".join(str(item) for item in value[:5])
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return JSONLStore(path).read_all()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_limited(path: Path, *, max_lines: int) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    clipped = lines[:max_lines]
    suffix = "\n\n... clipped ...\n" if len(lines) > max_lines else "\n"
    return "\n".join(clipped).rstrip() + suffix
