"""Explicit workspace scaffold helpers."""

from __future__ import annotations

from pathlib import Path

from careereng.career.resume.export import default_resume_template_text
from careereng.career.profile.intent_store import DEFAULT_INTENT
from careereng.career.profile.store import DEFAULT_PERSONA
from careereng.utils import dump_front_matter, ensure_dir, now_iso


USER_JOB_SKILL_FRONT_MATTER = {
    "id": "job-preferences",
    "name": "Job Preferences",
    "version": "v1",
    "updated_at": "",
    "scope": "job_preferences",
}

USER_JOB_SKILL_BODY = """# Job Preferences

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

APPLICATION_PROFILE = {
    "version": 1,
    "updated_at": "2026-06-05",
    "scope": "application_profile",
    "personal_information": {
        "gender": "Male",
        "country": "China",
        "state_province": "Shanxi",
        "city_town": "Taiyuan",
        "postal_code": "030000",
    },
    "work_authorization": {
        "default_country": "China",
        "legally_authorized_to_work": "Yes",
        "requires_visa_sponsorship": "No",
    },
    "compliance_defaults": {
        "standard_policy_acknowledgement": "Yes",
        "standard_rules_acknowledgement": "Yes",
    },
    "answer_policy": {
        "unknown_required_fact": "block_or_request_context",
    },
}

APPLICATION_PROFILE_BODY = """# Application Profile

Reusable facts for routine job-application forms.

This file is the canonical workspace-level source for standard application-form answers such as gender, address, work authorization, visa sponsorship, and routine compliance acknowledgements.

Keep career narrative, skills, projects, and experience in `persona.md`. Keep site-specific option labels and workflows in each site `SKILL.md`.
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
    _ensure_text_file(
        workspace / "profile" / "application_profile.md",
        dump_front_matter(APPLICATION_PROFILE, APPLICATION_PROFILE_BODY),
        rows,
        workspace,
    )
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

    canonical_preferences = workspace / "profile" / "job_preferences.md"
    legacy_skills = [
        workspace / "skills" / "jobs" / "SKILL.md",
        workspace / "jobs" / "SKILL.md",
    ]
    skill_text = default_user_job_skill_text()
    if not canonical_preferences.exists():
        for legacy_skill in legacy_skills:
            if legacy_skill.exists():
                skill_text = legacy_skill.read_text(encoding="utf-8")
                break
    else:
        skill_text = canonical_preferences.read_text(encoding="utf-8")
    _ensure_text_file(canonical_preferences, skill_text, rows, workspace)

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

    _ensure_dir(workspace / "assistant_bridge", rows, workspace)
    _ensure_dir(workspace / "assistant_bridge" / "context", rows, workspace)
    _ensure_empty_file(workspace / "assistant_bridge" / "intake_events.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "assistant_bridge" / "action_events.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "assistant_bridge" / "correction_events.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "assistant_bridge" / "routing_examples.jsonl", rows, workspace)
    _ensure_text_file(workspace / "assistant_bridge" / "thread_state.json", '{"threads": {}}\n', rows, workspace)
    _ensure_text_file(workspace / "assistant_bridge" / "intake_state.json", "{}\n", rows, workspace)

    _ensure_dir(workspace / "taskboard", rows, workspace)
    _ensure_dir(workspace / "taskboard" / "archive", rows, workspace)
    _ensure_empty_file(workspace / "taskboard" / "events.jsonl", rows, workspace)

    _ensure_dir(workspace / "action_cards", rows, workspace)
    _ensure_dir(workspace / "action_cards" / "open", rows, workspace)
    _ensure_dir(workspace / "action_cards" / "done", rows, workspace)
    _ensure_dir(workspace / "action_cards" / "cancelled", rows, workspace)
    _ensure_empty_file(workspace / "action_cards" / "index.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "action_cards" / "events.jsonl", rows, workspace)

    _ensure_dir(workspace / "memory", rows, workspace)
    _ensure_empty_file(workspace / "memory" / "memory_units.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "memory" / "profile_signals.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "memory" / "intent_signals.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "memory" / "application_feedback_signals.jsonl", rows, workspace)

    _ensure_dir(workspace / "interviews", rows, workspace)
    _ensure_empty_file(workspace / "interviews" / "sessions.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "interviews" / "events.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "interviews" / "candidates.jsonl", rows, workspace)

    _ensure_dir(workspace / "evolution", rows, workspace)
    _ensure_dir(workspace / "evolution" / "browser_control", rows, workspace)
    _ensure_empty_file(workspace / "evolution" / "browser_control" / "phase_events.jsonl", rows, workspace)
    _ensure_empty_file(workspace / "evolution" / "browser_control" / "lessons.jsonl", rows, workspace)
    _ensure_dir(workspace / "evolution" / "evidence", rows, workspace)
    _ensure_empty_file(workspace / "evolution" / "evidence" / "all.jsonl", rows, workspace)
    _ensure_dir(workspace / "evolution" / "candidates", rows, workspace)
    _ensure_empty_file(workspace / "evolution" / "candidates" / "open.jsonl", rows, workspace)
    _ensure_dir(workspace / "evolution" / "memory", rows, workspace)
    _ensure_empty_file(workspace / "evolution" / "memory" / "units.jsonl", rows, workspace)
    _ensure_dir(workspace / "evolution" / "reviews", rows, workspace)
    _ensure_dir(workspace / "evolution" / "context", rows, workspace)
    _ensure_dir(workspace / "evolution" / "runs", rows, workspace)
    _ensure_dir(workspace / "evolution" / "triggers", rows, workspace)
    _ensure_text_file(
        workspace / "evolution" / "triggers" / "site_workflow_state.json",
        '{"version": 1, "site_workflow": {}, "target_company_intelligence": {}, "assistant_router_memory_intake": {}, "updated_at": ""}\n',
        rows,
        workspace,
    )

    _ensure_dir(workspace / "runs", rows, workspace)
    _ensure_dir(workspace / "runs" / "daily", rows, workspace)

    _ensure_dir(workspace / "sites", rows, workspace)
    _ensure_empty_file(workspace / "sites" / "registry.jsonl", rows, workspace)

    return rows
