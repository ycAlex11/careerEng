"""Career-domain application planning and persistence operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from careereng.evolution.apply_probe import apply_probe_counters
from careereng.career.applications.skill_policy import load_job_skill_policies
from careereng.career.applications.application_store import ApplicationStore
from careereng.career.applications.planning_store import JobPlanningStore
from careereng.career.applications.job_store import JobStore


HistoryNormalizationCallback = Callable[[str, str, int], dict[str, Any]]


class ApplicationPlanningService:
    """Own application-plan and application-history operations.

    This service deliberately does not decide job fit or site workflow. Those
    decisions arrive in persisted job rows from the agent/Skill workflow; this
    layer only creates plans, selects their next actionable item, and persists
    resulting application state.
    """

    ACTIONABLE_PLAN_ACTIONS = {
        "open_for_match_review",
        "retry_blocked",
        "resume_application",
        "enrich_jd",
    }
    TERMINAL_APPLICATION_STATUSES = {
        "already_applied",
        "filtered_out",
        "submitted",
        "apply_failed",
        "blocked",
        "rejected",
        "closed",
        "withdrawn",
    }
    SUCCESSFUL_TERMINAL_APPLICATION_STATUSES = {
        "already_applied",
        "filtered_out",
        "submitted",
        "rejected",
        "closed",
        "withdrawn",
    }

    def __init__(
        self,
        *,
        project_root: Path,
        job_store: JobStore,
        application_store: ApplicationStore,
        site_store: Any,
    ):
        self.project_root = Path(project_root)
        self.job_store = job_store
        self.application_store = application_store
        self.site_store = site_store
        self.planning_store = JobPlanningStore(job_store.workspace)

    def run_job_rows(self, site_key: str, batch_id: str) -> list[dict[str, Any]]:
        list_run_jobs = getattr(self.site_store, "list_run_jobs", None)
        if not callable(list_run_jobs):
            return []
        return [row for row in list_run_jobs(site_key, batch_id) if isinstance(row, dict)]

    @staticmethod
    def fallback_run_job_identity(row: dict[str, Any]) -> str:
        for field in ("job_id", "canonical_job_id", "url"):
            value = str(row.get(field) or "").strip()
            if value:
                return f"{field}:{value}"
        title = str(row.get("title") or "").strip()
        location = str(row.get("location") or "").strip()
        posted_label = str(row.get("posted_label") or "").strip()
        return f"fallback:{title}|{location}|{posted_label}" if title else ""

    def run_job_identity(self, site_key: str, row: dict[str, Any]) -> str:
        identity_keys = getattr(self.site_store, "job_identity_keys", None)
        if callable(identity_keys):
            keys = identity_keys(site_key, row)
            if keys:
                return str(keys[0])
        return self.fallback_run_job_identity(row)

    def merged_run_job_rows(self, site_key: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged_by_key: dict[str, dict[str, Any]] = {}
        ordered_keys: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = self.run_job_identity(site_key, row)
            if not key:
                continue
            current = merged_by_key.get(key)
            if current is None:
                merged_by_key[key] = dict(row)
                ordered_keys.append(key)
                continue
            merged = dict(current)
            for field, value in row.items():
                if value is not None and value != "":
                    merged[field] = value
            merged_by_key[key] = merged
        return [merged_by_key[key] for key in ordered_keys if key in merged_by_key]

    def merged_run_job_rows_for_batch(self, site_key: str, batch_id: str) -> list[dict[str, Any]]:
        return self.merged_run_job_rows(site_key, self.run_job_rows(site_key, batch_id))

    @staticmethod
    def terminal_application_status(row: dict[str, Any]) -> str:
        application_status = str(row.get("application_status") or "").strip().lower()
        if application_status in ApplicationPlanningService.TERMINAL_APPLICATION_STATUSES:
            return application_status
        if str(row.get("decision_status") or "").strip().lower() == "already_applied":
            return "already_applied"
        return ""

    @classmethod
    def is_apply_row_terminal(cls, row: dict[str, Any]) -> bool:
        decision_status = str(row.get("decision_status") or "").strip().lower()
        application_status = cls.terminal_application_status(row)
        return decision_status in {"filtered_out", "already_applied"} or application_status in cls.TERMINAL_APPLICATION_STATUSES

    @classmethod
    def is_apply_row_success_terminal(cls, row: dict[str, Any]) -> bool:
        decision_status = str(row.get("decision_status") or "").strip().lower()
        application_status = cls.terminal_application_status(row)
        return decision_status in {"filtered_out", "already_applied"} or application_status in cls.SUCCESSFUL_TERMINAL_APPLICATION_STATUSES

    def apply_counters_from_run(self, site_key: str, batch_id: str) -> dict[str, int]:
        return apply_probe_counters(self.merged_run_job_rows_for_batch(site_key, batch_id))

    @staticmethod
    def apply_counter_payload(counters: dict[str, int]) -> dict[str, int]:
        return {
            "attempted": int(counters.get("attempted") or 0),
            "form_sampled": int(counters.get("form_sampled") or 0),
            "form_successful": int(counters.get("form_successful") or 0),
            "form_unsuccessful": int(counters.get("form_unsuccessful") or 0),
            "apply_path_attempted": int(counters.get("apply_path_attempted") or 0),
            "submitted": int(counters.get("submitted") or 0),
            "already_applied": int(counters.get("already_applied") or 0),
            "filtered_out": int(counters.get("filtered_out") or 0),
            "failed": int(counters.get("failed") or 0),
            "blocked": int(counters.get("blocked") or 0),
            "excluded_role_violations": int(counters.get("excluded_role_violations") or 0),
        }

    @staticmethod
    def apply_plan_counts(plan: dict[str, Any]) -> dict[str, int]:
        counts = plan.get("counts") if isinstance(plan.get("counts"), dict) else {}
        normalized: dict[str, int] = {}
        for key, value in counts.items():
            try:
                normalized[str(key)] = int(value or 0)
            except Exception:
                normalized[str(key)] = 0
        return normalized

    @staticmethod
    def aggregate_apply_status(counters: dict[str, int]) -> str:
        if int(counters.get("failed") or 0):
            return "failed"
        if int(counters.get("blocked") or 0):
            return "blocked"
        return "done"

    def write_retrieval_snapshot(self, *, site_key: str, batch_id: str, current: dict[str, Any]) -> dict[str, Any]:
        rows = self.merged_run_job_rows_for_batch(site_key, batch_id)
        if not rows:
            return {}
        retrieve = current.get("retrieve") if isinstance(current.get("retrieve"), dict) else {}
        snapshot = self.planning_store.write_snapshot(
            site_key=site_key,
            batch_id=batch_id,
            jobs=rows,
            current_url=str(current.get("current_url") or current.get("entry_url") or ""),
            retrieval_complete=str(retrieve.get("status") or "") == "done",
            result_count=int(retrieve.get("count") or len(rows)),
            filters_summary={
                "current_url": str(current.get("current_url") or ""),
                "skill_path": str(current.get("skill_path") or ""),
            },
            stop_reason=str(current.get("reason_tag") or ""),
        )
        save_run_context = getattr(self.site_store, "save_run_context", None)
        if callable(save_run_context):
            save_run_context(
                site_key,
                batch_id,
                {
                    "latest_search_snapshot": {
                        "snapshot_id": str(snapshot.get("snapshot_id") or ""),
                        "search_fingerprint": str(snapshot.get("search_fingerprint") or ""),
                        "path": str(snapshot.get("path") or ""),
                    }
                },
            )
        self.job_store.append_event(
            "job_search_snapshot.written",
            {
                "batch_id": batch_id,
                "site_key": site_key,
                "snapshot_id": str(snapshot.get("snapshot_id") or ""),
                "search_fingerprint": str(snapshot.get("search_fingerprint") or ""),
                "retrieval_complete": bool(snapshot.get("retrieval_complete")),
                "result_count": int(snapshot.get("result_count") or 0),
            },
        )
        return snapshot

    def latest_search_snapshot_id(self, *, site_key: str, batch_id: str) -> str:
        load_run_context = getattr(self.site_store, "load_run_context", None)
        context = load_run_context(site_key, batch_id) if callable(load_run_context) else {}
        latest = context.get("latest_search_snapshot") if isinstance(context, dict) else {}
        return str(latest.get("snapshot_id") or "") if isinstance(latest, dict) else ""

    def decision_context_hash(self, site_key: str) -> str:
        context_hash = getattr(self.site_store, "decision_context_hash", None)
        if not callable(context_hash):
            return ""
        try:
            return str(context_hash(site_key) or "")
        except Exception:
            return ""

    def decision_context_versions(self, site_key: str) -> dict[str, str]:
        context_versions = getattr(self.site_store, "decision_context_versions", None)
        if not callable(context_versions):
            return {}
        try:
            payload = context_versions(site_key)
        except Exception:
            return {}
        return {str(key): str(value) for key, value in payload.items()} if isinstance(payload, dict) else {}

    def normalize_history_for_apply_plan(
        self,
        *,
        site_key: str,
        batch_id: str,
        on_needs_review: HistoryNormalizationCallback | None = None,
    ) -> dict[str, Any]:
        normalize = getattr(self.site_store, "normalize_history_decision_metadata", None)
        if not callable(normalize):
            return {}
        try:
            result = normalize(site_key, max_rows=10)
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}
        if not isinstance(result, dict):
            return {}
        if str(result.get("status") or "") == "needs_review" and on_needs_review:
            card = on_needs_review(site_key, batch_id, int(result.get("count") or 0))
            result["action_card_id"] = str(card.get("card_id") or "") if isinstance(card, dict) else ""
        return result

    def ensure_apply_plan(
        self,
        *,
        site_key: str,
        batch_id: str,
        session_id: str,
        turn_id: str,
        on_history_normalization_needs_review: HistoryNormalizationCallback | None = None,
    ) -> dict[str, Any]:
        plan = self.planning_store.load_apply_plan(batch_id=batch_id, site_key=site_key)
        if not plan.get("plan_items"):
            normalization = self.normalize_history_for_apply_plan(
                site_key=site_key,
                batch_id=batch_id,
                on_needs_review=on_history_normalization_needs_review,
            )
            rows = self.merged_run_job_rows_for_batch(site_key, batch_id)
            context_versions = self.decision_context_versions(site_key)
            decision_context_hash = self.decision_context_hash(site_key)
            skill_policies = load_job_skill_policies(self.project_root, site_key)
            apply_candidate_policy = skill_policies.get("apply_candidate_policy", {})
            match_history_rows = getattr(self.site_store, "match_history_rows", None)
            if callable(match_history_rows):
                try:
                    history_matches = match_history_rows(site_key, rows, batch_id=batch_id)
                except TypeError:
                    history_matches = match_history_rows(site_key, rows)
            else:
                history_matches = []
            plan = self.planning_store.write_apply_plan(
                site_key=site_key,
                batch_id=batch_id,
                jobs=rows,
                history_matches=history_matches,
                snapshot_id=self.latest_search_snapshot_id(site_key=site_key, batch_id=batch_id),
                apply_requested=True,
                decision_context_hash=decision_context_hash,
                context_versions=context_versions,
                apply_candidate_policy=apply_candidate_policy,
            )
            if normalization:
                plan["normalization"] = normalization
            self.job_store.append_event(
                "job_apply_plan.written",
                {
                    "batch_id": batch_id,
                    "site_key": site_key,
                    "plan_id": str(plan.get("plan_id") or ""),
                    "snapshot_id": str(plan.get("snapshot_id") or ""),
                    "counts": plan.get("counts") if isinstance(plan.get("counts"), dict) else {},
                    "normalization": normalization if isinstance(normalization, dict) else {},
                },
            )
        terminal_updates = [
            update
            for item in plan.get("plan_items", [])
            if isinstance(item, dict)
            for update in [self.planning_store.terminal_update_for_plan_item(item)]
            if update
        ]
        if terminal_updates:
            self.site_store.update_run_jobs(site_key, terminal_updates, session_id, turn_id, batch_id)
        return plan

    def pending_apply_rows(self, site_key: str, batch_id: str) -> list[dict[str, Any]]:
        rows = [row for row in self.merged_run_job_rows_for_batch(site_key, batch_id) if not self.is_apply_row_terminal(row)]
        plan = self.planning_store.load_apply_plan(batch_id=batch_id, site_key=site_key)
        items = plan.get("plan_items") if isinstance(plan.get("plan_items"), list) else []
        actionable_ids = {
            str(item.get("job_id") or "")
            for item in items
            if isinstance(item, dict)
            and str(item.get("action") or "") in self.ACTIONABLE_PLAN_ACTIONS
            and str(item.get("job_id") or "")
        }
        if not actionable_ids:
            return rows
        return [row for row in rows if str(row.get("job_id") or "") in actionable_ids]

    def seal_apply_row_terminal(
        self,
        *,
        site_key: str,
        batch_id: str,
        session_id: str,
        turn_id: str,
        job_id: str,
        status: str,
        error_text: str = "",
    ) -> None:
        self.site_store.update_run_jobs(
            site_key,
            [
                {
                    "job_id": str(job_id or ""),
                    "application_status": status,
                    "last_apply_error": str(error_text or ""),
                }
            ],
            session_id,
            turn_id,
            batch_id,
        )

    def promote_retrieved_run_to_history(self, *, site_key: str, batch_id: str) -> None:
        promote = getattr(self.site_store, "promote_run_jobs_to_history", None)
        if callable(promote):
            promote(site_key, batch_id)

    def promote_apply_run_to_history(
        self,
        *,
        site_key: str,
        batch_id: str,
        session_id: str,
        turn_id: str,
    ) -> None:
        self.promote_retrieved_run_to_history(site_key=site_key, batch_id=batch_id)
        run_rows = self.run_job_rows(site_key, batch_id)
        update_decisions = getattr(self.site_store, "update_job_decisions", None)
        if callable(update_decisions):
            update_decisions(site_key, run_rows)

        applications: list[dict[str, Any]] = []
        for row in run_rows:
            status = self.terminal_application_status(row)
            if not status:
                continue
            detail: dict[str, Any] = {}
            error_text = str(row.get("last_apply_error") or "").strip()
            if error_text:
                detail["error"] = error_text
            applications.append(
                {
                    "job_id": str(row.get("job_id") or ""),
                    "canonical_job_id": str(row.get("canonical_job_id") or ""),
                    "status": status,
                    "submitted": status == "submitted",
                    "site_id": site_key,
                    "batch_id": batch_id,
                    "title": str(row.get("title") or ""),
                    "url": str(row.get("url") or ""),
                    "decision_context_hash": str(row.get("decision_context_hash") or ""),
                    "decision_reason_type": str(row.get("decision_reason_type") or ""),
                    "context_versions": row.get("context_versions") if isinstance(row.get("context_versions"), dict) else {},
                    "detail": detail,
                }
            )
        update_outcomes = getattr(self.site_store, "update_job_application_outcomes", None)
        if callable(update_outcomes):
            update_outcomes(site_key, applications)
        append_site_apps = getattr(self.site_store, "append_applications", None)
        if callable(append_site_apps):
            append_site_apps(site_key, applications, session_id, turn_id)
        for app in applications:
            self.application_store.append_application(
                {
                    "site_id": site_key,
                    "batch_id": batch_id,
                    "session_id": session_id,
                    "turn_id": turn_id,
                    **app,
                }
            )

    def _row_identity_key_set(self, site_key: str, row: dict[str, Any]) -> set[str]:
        identity_keys = getattr(self.site_store, "job_identity_keys", None)
        if callable(identity_keys):
            return {str(key) for key in identity_keys(site_key, row) if str(key)}
        fallback = self.fallback_run_job_identity(row)
        return {fallback} if fallback else set()
