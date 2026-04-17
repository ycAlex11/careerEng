"""Explicit workspace scaffold helpers."""

from __future__ import annotations

from pathlib import Path

from careereng.resume.export import default_resume_template_text
from careereng.storage.intent_store import DEFAULT_INTENT
from careereng.storage.profile_store import DEFAULT_PERSONA
from careereng.utils import dump_front_matter, ensure_dir, now_iso


USER_JOB_SKILL_FRONT_MATTER = {
    "id": "search-jobs",
    "name": "Search Jobs Skill",
    "version": "v1",
    "updated_at": "",
    "scope": "jobs",
}

USER_JOB_SKILL_BODY = """# Search Jobs Skill

Use this file for user-owned job search preferences.
When this file conflicts with `intent.md`, this file should win.

## Company Preferences

- Prioritize Western foreign employers, especially US or Europe technology companies.
- Prioritize established engineering organizations rather than very small local companies.
- Treat foreign companies hiring in China or operating China offices as valid targets.
- Do not recommend mainland Chinese domestic companies unless the user explicitly asks for them.
- Treat AI-related business lines as a bonus, not a hard requirement, during the company-finding stage.

## Employment Preferences

- Prioritize full-time experienced-hire roles.
- Do not prioritize remote-only roles.
- Prefer onsite or hybrid work when possible.

## Role Preferences

- Prioritize software engineering, backend, platform, systems, and architecture-adjacent roles first.
- Treat AI agentic systems, LLM application engineering, and related AI application roles as strong bonuses.
- Use role preferences mainly when narrowing concrete jobs inside already selected companies.
"""


WorkspaceEntry = dict[str, str]


def default_user_job_skill_text() -> str:
    payload = dict(USER_JOB_SKILL_FRONT_MATTER)
    payload["updated_at"] = now_iso()[:10]
    return dump_front_matter(payload, USER_JOB_SKILL_BODY)


def _ensure_dir(path: Path, rows: list[WorkspaceEntry], workspace: Path) -> None:
    existed = path.exists()
    ensure_dir(path)
    rows.append(
        {
            "path": str(path.relative_to(workspace)),
            "kind": "dir",
            "status": "existing" if existed else "created",
        }
    )


def _ensure_text_file(path: Path, text: str, rows: list[WorkspaceEntry], workspace: Path) -> None:
    existed = path.exists()
    ensure_dir(path.parent)
    if not existed:
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
    rows.append(
        {
            "path": str(path.relative_to(workspace)),
            "kind": "file",
            "status": "existing" if existed else "created",
        }
    )


def _ensure_empty_file(path: Path, rows: list[WorkspaceEntry], workspace: Path) -> None:
    _ensure_text_file(path, "", rows, workspace)


def bootstrap_workspace(workspace: Path) -> list[WorkspaceEntry]:
    workspace = ensure_dir(workspace)
    rows: list[WorkspaceEntry] = []

    _ensure_dir(workspace / "sessions", rows, workspace)
    _ensure_dir(workspace / "sessions_state", rows, workspace)

    _ensure_dir(workspace / "chat", rows, workspace)
    _ensure_dir(workspace / "chat" / "daily", rows, workspace)
    _ensure_empty_file(workspace / "chat" / "all.jsonl", rows, workspace)

    _ensure_dir(workspace / "profile", rows, workspace)
    _ensure_dir(workspace / "profile" / "history", rows, workspace)
    _ensure_dir(workspace / "profile" / "reports", rows, workspace)
    _ensure_dir(workspace / "profile" / "sources", rows, workspace)
    _ensure_text_file(workspace / "profile" / "persona.md", dump_front_matter(DEFAULT_PERSONA), rows, workspace)
    _ensure_empty_file(workspace / "profile" / "profile_events.jsonl", rows, workspace)

    _ensure_dir(workspace / "cv", rows, workspace)
    _ensure_dir(workspace / "cv" / "current", rows, workspace)
    _ensure_dir(workspace / "cv" / "history", rows, workspace)
    _ensure_dir(workspace / "cv" / "templates", rows, workspace)
    _ensure_dir(workspace / "cv" / "exports", rows, workspace)
    _ensure_dir(workspace / "cv" / "variants", rows, workspace)
    _ensure_text_file(
        workspace / "cv" / "templates" / "default.typ",
        default_resume_template_text(),
        rows,
        workspace,
    )

    _ensure_dir(workspace / "intent", rows, workspace)
    _ensure_dir(workspace / "intent" / "history", rows, workspace)
    _ensure_dir(workspace / "intent" / "reports", rows, workspace)
    _ensure_text_file(workspace / "intent" / "intent.md", dump_front_matter(DEFAULT_INTENT), rows, workspace)
    _ensure_empty_file(workspace / "intent" / "intent_events.jsonl", rows, workspace)

    _ensure_dir(workspace / "skills", rows, workspace)
    _ensure_dir(workspace / "skills" / "jobs", rows, workspace)
    canonical_skill = workspace / "skills" / "jobs" / "SKILL.md"
    legacy_skill = workspace / "jobs" / "SKILL.md"
    if not canonical_skill.exists() and legacy_skill.exists():
        skill_text = legacy_skill.read_text(encoding="utf-8")
    else:
        skill_text = default_user_job_skill_text()
    _ensure_text_file(canonical_skill, skill_text, rows, workspace)

    _ensure_dir(workspace / "search", rows, workspace)
    _ensure_empty_file(workspace / "search" / "queries.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "search" / "web_results.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "search" / "company_candidates.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "search" / "company_decisions.jsonl", rows, workspace)

    _ensure_dir(workspace / "applications", rows, workspace)
    _ensure_empty_file(workspace / "applications" / "all.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "applications" / "events.jsonl", rows, workspace)

    _ensure_dir(workspace / "jobs", rows, workspace)
    _ensure_dir(workspace / "jobs" / "batches", rows, workspace)
    _ensure_empty_file(workspace / "jobs" / "events.jsonl", rows, workspace)

    _ensure_dir(workspace / "router", rows, workspace)
    _ensure_empty_file(workspace / "router" / "events.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "router" / "feedback.jsonl", rows, workspace)

    _ensure_dir(workspace / "runs", rows, workspace)
    _ensure_dir(workspace / "runs" / "daily", rows, workspace)

    _ensure_dir(workspace / "sites", rows, workspace)
    _ensure_empty_file(workspace / "sites" / "registry.jsonl", rows, workspace)

    return rows
