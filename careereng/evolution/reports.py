"""Evolution run reports generated from runtime probe outcomes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from careereng.orchestration.context.registry import BrowserContextRegistry
from careereng.evolution.apply_probe import apply_probe_counters, excluded_role_violations
from careereng.platform.reporting import JsonMarkdownArtifact, JsonMarkdownArtifactPaths, ReportArtifactStore
from careereng.utils import make_id, now_iso


APPLICATION_TERMINAL_UNSUCCESSFUL = {"apply_failed", "blocked"}
APPLICATION_TERMINAL_SUCCESS = {"submitted", "already_applied"}


class ApplyProbeReport(JsonMarkdownArtifact):
    """Evolution report for an apply workflow probe."""

    def __init__(
        self,
        *,
        workspace: Path | str,
        project_root: Path | str,
        batch_id: str,
        site_key: str,
        site_name: str,
        site_row: dict[str, Any],
        counters: dict[str, int],
        run_rows: list[dict[str, Any]],
        stop_reason: str,
        max_attempted: int,
        unsuccessful_threshold: int,
        run_id: str | None = None,
        created_at: str | None = None,
    ):
        self.workspace_path = Path(workspace)
        self.project_root = Path(project_root)
        self.batch_id = str(batch_id or "")
        self.site_key = str(site_key or "")
        self.site_name = str(site_name or self.site_key or "")
        self.site_row = site_row if isinstance(site_row, dict) else {}
        self.counters = counters if isinstance(counters, dict) else {}
        self.run_rows = list(run_rows or [])
        self.stop_reason = str(stop_reason or "")
        self.max_attempted = int(max_attempted or 0)
        self.unsuccessful_threshold = int(unsuccessful_threshold or 0)
        self.run_id = run_id or make_id("evo_run")
        self.created_at = created_at or now_iso()
        run_dir = self.workspace_path / "evolution" / "runs" / self.run_id
        super().__init__(
            paths=JsonMarkdownArtifactPaths(
                json_path=run_dir / "report.json",
                markdown_path=run_dir / "report.md",
            ),
            store=ReportArtifactStore(self.workspace_path),
            artifact_id=f"evolution_apply_probe:{self.run_id}",
            domain="evolution",
            report_type="apply_probe",
            metadata={
                "run_id": self.run_id,
                "batch_id": self.batch_id,
                "site_key": self.site_key,
                "phase": "apply",
            },
        )

    def build_payload(self) -> dict[str, Any]:
        apply_facts = _load_apply_facts(self.workspace_path)
        derived_counters = apply_probe_counters(self.run_rows)
        counters = {**derived_counters, **self.counters}
        blockers = _build_blockers(run_rows=self.run_rows)
        violations = excluded_role_violations(self.run_rows)
        repeated_blockers = [row for row in blockers if int(row.get("count") or 0) > 1]
        missing_fields = sorted({field for row in blockers for field in row.get("fields", [])})
        missing_fact_paths = _missing_fact_paths(missing_fields, apply_facts)
        next_action = _next_action(
            blockers=blockers,
            excluded_role_violations=violations,
            missing_fields=missing_fields,
            missing_fact_paths=missing_fact_paths,
            stop_reason=self.stop_reason,
            counters=counters,
            max_attempted=self.max_attempted,
            unsuccessful_threshold=self.unsuccessful_threshold,
        )
        status = _report_status(
            next_action=next_action,
            stop_reason=self.stop_reason,
            counters=counters,
            max_attempted=self.max_attempted,
            unsuccessful_threshold=self.unsuccessful_threshold,
        )
        auto_accept = _auto_accept_payload(
            status=status,
            next_action=next_action,
            stop_reason=self.stop_reason,
            counters=counters,
            max_attempted=self.max_attempted,
            unsuccessful_threshold=self.unsuccessful_threshold,
            excluded_role_violations=violations,
            missing_fact_paths=missing_fact_paths,
        )

        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "candidate_id": "apply_form_workflow",
            "candidate": {
                "target_type": "site_apply_workflow",
                "target_ref": f"skills/search/jobs/sites/{self.site_key}/SKILL.md",
            },
            "scope": {
                "batch_id": self.batch_id,
                "site_key": self.site_key,
                "site_name": self.site_name,
                "phase": "apply",
            },
            "status": status,
            "stop_reason": self.stop_reason,
            "budget": {
                "max_apply_probe_jobs": self.max_attempted,
                "max_apply_probe_form_samples": self.max_attempted,
                "stop_unsuccessful_threshold": self.unsuccessful_threshold,
            },
            "metrics": {
                "retrieved": int(counters.get("retrieved") or 0),
                "attempted": int(counters.get("attempted") or 0),
                "form_sampled": int(counters.get("form_sampled") or counters.get("attempted") or 0),
                "form_successful": int(counters.get("form_successful") or counters.get("submitted") or 0),
                "form_unsuccessful": int(counters.get("form_unsuccessful") or 0),
                "apply_path_attempted": int(counters.get("apply_path_attempted") or 0),
                "successful": int(counters.get("form_successful") or counters.get("submitted") or 0),
                "submitted": int(counters.get("submitted") or 0),
                "already_applied": int(counters.get("already_applied") or 0),
                "unsuccessful": int(counters.get("form_unsuccessful") or 0),
                "failed": int(counters.get("failed") or 0),
                "blocked": int(counters.get("blocked") or 0),
                "filtered_out": int(counters.get("filtered_out") or 0),
                "excluded_role_violations": len(violations),
            },
            "auto_accept": auto_accept,
            "excluded_role_violations": violations,
            "blockers": blockers,
            "repeated_blockers": repeated_blockers,
            "missing_fields": missing_fields,
            "missing_fact_paths": missing_fact_paths,
            "available_facts_summary": _facts_summary(apply_facts),
            "next_action": next_action,
            "paths": {
                "project_root": str(self.project_root),
                "report_json": str(self.paths.json_path),
                "report_md": str(self.paths.markdown_path),
                "site_skill": str(
                    self.project_root / "skills" / "search" / "jobs" / "sites" / self.site_key / "SKILL.md"
                ),
                "batch": str(self.workspace_path / "jobs" / "batches" / f"{self.batch_id}.json"),
            },
            "site_row_snapshot": self.site_row,
        }

    def render_markdown(self, payload: dict[str, Any]) -> str:
        return _render_apply_probe_report(payload)


def create_apply_probe_report(
    *,
    workspace: Path | str,
    project_root: Path | str,
    batch_id: str,
    site_key: str,
    site_name: str,
    site_row: dict[str, Any],
    counters: dict[str, int],
    run_rows: list[dict[str, Any]],
    stop_reason: str,
    max_attempted: int,
    unsuccessful_threshold: int,
) -> dict[str, Any]:
    return ApplyProbeReport(
        workspace=workspace,
        project_root=project_root,
        batch_id=batch_id,
        site_key=site_key,
        site_name=site_name,
        site_row=site_row,
        counters=counters,
        run_rows=run_rows,
        stop_reason=stop_reason,
        max_attempted=max_attempted,
        unsuccessful_threshold=unsuccessful_threshold,
    ).write()


def _load_apply_facts(workspace: Path) -> dict[str, Any]:
    try:
        registry = BrowserContextRegistry(workspace)
    except Exception:
        return {}
    facts = registry.apply_facts
    return facts if isinstance(facts, dict) else {}


def _build_blockers(*, run_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in run_rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("application_status") or "").strip().lower()
        if status not in APPLICATION_TERMINAL_UNSUCCESSFUL:
            continue
        error_text = str(row.get("last_apply_error") or row.get("apply_error") or "").strip()
        if not error_text:
            error_text = status
        fields = _extract_field_names(error_text)
        signature = "|".join(fields) if fields else _normalize_error_signature(error_text)
        current = grouped.setdefault(
            signature,
            {
                "signature": signature,
                "count": 0,
                "status": status,
                "fields": fields,
                "example_error": error_text,
                "jobs": [],
            },
        )
        current["count"] = int(current.get("count") or 0) + 1
        jobs = current.get("jobs") if isinstance(current.get("jobs"), list) else []
        jobs.append(
            {
                "job_id": str(row.get("job_id") or ""),
                "site_job_id": str(row.get("site_job_id") or ""),
                "title": str(row.get("title") or ""),
                "url": str(row.get("url") or ""),
            }
        )
        current["jobs"] = jobs
    return sorted(grouped.values(), key=lambda row: int(row.get("count") or 0), reverse=True)


def _extract_field_names(text: str) -> list[str]:
    field_names = {
        "state/province": ("state/province", "state", "province"),
        "city/town": ("city/town", "city", "town"),
        "zip/postal code": ("zip/postal code", "zip code", "postal code", "postcode"),
        "country": ("country",),
        "phone": ("phone", "phone number", "mobile"),
        "email": ("email", "email address"),
        "address": ("address", "street address"),
        "linkedin": ("linkedin", "linkedIn profile"),
    }
    lowered = str(text or "").lower()
    found = [label for label, aliases in field_names.items() if any(alias.lower() in lowered for alias in aliases)]
    return sorted(set(found))


def _normalize_error_signature(text: str) -> str:
    normalized = re.sub(r"https?://\S+", "<url>", str(text or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized[:160] or "unknown_blocker"


def _missing_fact_paths(fields: list[str], apply_facts: dict[str, Any]) -> list[str]:
    field_paths = {
        "state/province": ("contact.address.state_province",),
        "city/town": ("contact.address.city_town", "basic.current_city"),
        "zip/postal code": ("contact.address.postal_code",),
        "country": ("contact.address.country", "basic.nationality"),
        "phone": ("contact.phone", "phone"),
        "email": ("contact.email", "email"),
        "address": ("contact.address", "location"),
        "linkedin": ("contact.linkedin", "linkedin"),
    }
    missing: list[str] = []
    for field in fields:
        paths = field_paths.get(field, ())
        if not paths:
            missing.append(field)
            continue
        if not any(_get_path(apply_facts, path) not in ("", [], {}, None) for path in paths):
            missing.extend(paths[:1])
    return sorted(set(missing))


def _get_path(data: dict[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _next_action(
    *,
    blockers: list[dict[str, Any]],
    excluded_role_violations: list[dict[str, Any]],
    missing_fields: list[str],
    missing_fact_paths: list[str],
    stop_reason: str,
    counters: dict[str, int],
    max_attempted: int,
    unsuccessful_threshold: int,
) -> dict[str, Any]:
    if excluded_role_violations:
        return {
            "type": "propose_site_skill_patch",
            "reason": "Apply probe let an excluded intern/campus/new-grad role enter the apply workflow. Refine filtering/apply rules before accepting this workflow.",
            "fields": [],
            "missing_fact_paths": [],
            "violation_count": len(excluded_role_violations),
        }
    if missing_fields and missing_fact_paths:
        return {
            "type": "ask_user_fact",
            "reason": "Apply probe found required form fields that are not available in profile/apply_facts.",
            "fields": missing_fields,
            "missing_fact_paths": missing_fact_paths,
        }
    if missing_fields:
        return {
            "type": "propose_site_skill_patch",
            "reason": "Apply probe found repeatable required field mapping; available facts appear sufficient, so the site skill likely needs clearer form-filling instructions.",
            "fields": missing_fields,
            "missing_fact_paths": [],
        }
    if str(stop_reason or "") == "max_attempted_reached":
        form_sampled = int(counters.get("form_sampled") or 0)
        form_unsuccessful = int(counters.get("form_unsuccessful") or 0)
        if form_sampled >= max(0, int(max_attempted or 0)) and form_unsuccessful < max(
            1, int(unsuccessful_threshold or 1)
        ):
            return {
                "type": "evaluate_acceptance",
                "reason": "Apply form probe reached its form-sample budget without crossing the unsuccessful threshold.",
                "fields": [],
                "missing_fact_paths": [],
            }
    if any(str(row.get("status") or "") == "blocked" for row in blockers):
        return {
            "type": "inspect_blocker",
            "reason": "Apply probe ended with blockers but no stable required-field names were extracted.",
            "fields": [],
            "missing_fact_paths": [],
        }
    return {
        "type": "inspect_runtime_or_page",
        "reason": "Apply probe failed without a classified missing profile fact or field-mapping candidate.",
        "fields": [],
        "missing_fact_paths": [],
    }


def _report_status(
    *,
    next_action: dict[str, Any],
    stop_reason: str,
    counters: dict[str, int],
    max_attempted: int,
    unsuccessful_threshold: int,
) -> str:
    action_type = str(next_action.get("type") or "")
    if action_type == "ask_user_fact":
        return "needs_user_fact"
    if action_type == "propose_site_skill_patch":
        return "needs_skill_refinement"
    if action_type in {"inspect_blocker", "inspect_runtime_or_page"}:
        return "needs_review"
    if action_type == "evaluate_acceptance" and str(stop_reason or "") == "max_attempted_reached":
        form_sampled = int(counters.get("form_sampled") or 0)
        form_unsuccessful = int(counters.get("form_unsuccessful") or 0)
        if form_sampled >= max(0, int(max_attempted or 0)) and form_unsuccessful < max(1, int(unsuccessful_threshold or 1)):
            return "success"
        return "keep_observing"
    return "needs_review"


def _auto_accept_payload(
    *,
    status: str,
    next_action: dict[str, Any],
    stop_reason: str,
    counters: dict[str, int],
    max_attempted: int,
    unsuccessful_threshold: int,
    excluded_role_violations: list[dict[str, Any]],
    missing_fact_paths: list[str],
) -> dict[str, Any]:
    form_sampled = int(counters.get("form_sampled") or 0)
    form_unsuccessful = int(counters.get("form_unsuccessful") or 0)
    eligible = bool(
        status == "success"
        and str(next_action.get("type") or "") == "evaluate_acceptance"
        and str(stop_reason or "") == "max_attempted_reached"
        and form_sampled >= max(0, int(max_attempted or 0))
        and form_unsuccessful < max(1, int(unsuccessful_threshold or 1))
        and not excluded_role_violations
        and not missing_fact_paths
    )
    return {
        "eligible": eligible,
        "status": "accepted" if eligible else "not_eligible",
        "reason": (
            "Form workflow reached the probe sample budget within the unsuccessful threshold."
            if eligible
            else "Probe did not satisfy automatic acceptance gates."
        ),
        "gates": {
            "form_sampled": form_sampled,
            "required_form_samples": int(max_attempted or 0),
            "form_unsuccessful": form_unsuccessful,
            "unsuccessful_threshold": int(unsuccessful_threshold or 0),
            "excluded_role_violations": len(excluded_role_violations),
            "missing_fact_paths": len(missing_fact_paths),
        },
    }


def _facts_summary(apply_facts: dict[str, Any]) -> dict[str, Any]:
    contact = apply_facts.get("contact") if isinstance(apply_facts.get("contact"), dict) else {}
    address = contact.get("address") if isinstance(contact.get("address"), dict) else {}
    basic = apply_facts.get("basic") if isinstance(apply_facts.get("basic"), dict) else {}
    constraints = apply_facts.get("constraints") if isinstance(apply_facts.get("constraints"), dict) else {}
    return {
        "has_contact": bool(contact),
        "address": {key: address.get(key) for key in ("country", "state_province", "city_town", "postal_code") if address.get(key)},
        "basic": {key: basic.get(key) for key in ("name", "nationality", "current_city") if basic.get(key)},
        "constraints": constraints,
    }


def _render_apply_probe_report(payload: dict[str, Any]) -> str:
    scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
    next_action = payload.get("next_action") if isinstance(payload.get("next_action"), dict) else {}
    auto_accept = payload.get("auto_accept") if isinstance(payload.get("auto_accept"), dict) else {}
    lines = [
        "# Apply Evolution Probe Report",
        "",
        f"- Run: `{payload.get('run_id')}`",
        f"- Candidate: `{payload.get('candidate_id')}`",
        f"- Status: `{payload.get('status')}`",
        f"- Stop Reason: `{payload.get('stop_reason')}`",
        f"- Site: `{scope.get('site_key')}` {scope.get('site_name') or ''}".rstrip(),
        f"- Batch: `{scope.get('batch_id')}`",
        "",
        "## Budget",
        "",
        f"- Max apply form samples: {budget.get('max_apply_probe_form_samples') or budget.get('max_apply_probe_jobs')}",
        f"- Stop unsuccessful threshold: {budget.get('stop_unsuccessful_threshold')}",
        "",
        "## Metrics",
        "",
        f"- Retrieved jobs: {metrics.get('retrieved')}",
        f"- Form sampled: {metrics.get('form_sampled')} (counts toward probe budget)",
        f"- Form successful: {metrics.get('form_successful')}",
        f"- Form unsuccessful: {metrics.get('form_unsuccessful')}",
        f"- Apply-path attempted: {metrics.get('apply_path_attempted')} (informational)",
        f"- Submitted: {metrics.get('submitted')}",
        f"- Already applied: {metrics.get('already_applied')} (not counted as form sample)",
        f"- Failed: {metrics.get('failed')}",
        f"- Blocked: {metrics.get('blocked')}",
        f"- Filtered out: {metrics.get('filtered_out')} (not counted as unsuccessful)",
        f"- Excluded-role violations: {metrics.get('excluded_role_violations')}",
        "",
        "## Auto Acceptance",
        "",
        f"- Eligible: `{bool(auto_accept.get('eligible'))}`",
        f"- Status: `{auto_accept.get('status') or ''}`",
        f"- Reason: {auto_accept.get('reason') or ''}",
        "",
        "## Repeated Blockers",
        "",
    ]
    blockers = payload.get("repeated_blockers")
    if isinstance(blockers, list) and blockers:
        for blocker in blockers:
            fields = ", ".join(blocker.get("fields") or []) or "unclassified"
            lines.append(f"- count={blocker.get('count')} fields={fields}: {blocker.get('example_error')}")
    else:
        lines.append("- None")
    violations = payload.get("excluded_role_violations")
    lines.extend(
        [
            "",
            "## Excluded Role Violations",
            "",
        ]
    )
    if isinstance(violations, list) and violations:
        for violation in violations:
            lines.append(
                f"- {violation.get('title') or 'Untitled'} "
                f"status={violation.get('application_status') or ''} url={violation.get('url') or ''}"
            )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Missing Fields",
            "",
        ]
    )
    missing_fields = payload.get("missing_fields")
    if isinstance(missing_fields, list) and missing_fields:
        for field in missing_fields:
            lines.append(f"- {field}")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Available Facts Summary",
            "",
            "```json",
            json.dumps(payload.get("available_facts_summary") or {}, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Next Action",
            "",
            f"- Type: `{next_action.get('type') or ''}`",
            f"- Reason: {next_action.get('reason') or ''}",
        ]
    )
    missing_paths = next_action.get("missing_fact_paths") if isinstance(next_action, dict) else []
    if isinstance(missing_paths, list) and missing_paths:
        lines.append(f"- Missing fact paths: {', '.join(str(item) for item in missing_paths)}")
    return "\n".join(lines).rstrip() + "\n"
