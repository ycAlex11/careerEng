"""Generic ranked-application state and queue helpers."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


RANKING_PENDING = "ranking_pending"
READY_TO_APPLY = "ready_to_apply"
DEFERRED_BY_RANK = "deferred_by_rank"

RANKED_REVIEW_STATES = frozenset({RANKING_PENDING, READY_TO_APPLY, DEFERRED_BY_RANK})


def normalized_apply_state(row: dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("apply_state") or "").strip().lower()


def is_ranked_review_complete(row: dict[str, Any] | None) -> bool:
    return normalized_apply_state(row) in RANKED_REVIEW_STATES


def is_ready_to_apply(row: dict[str, Any] | None) -> bool:
    return normalized_apply_state(row) == READY_TO_APPLY


def ranking_limit(row: dict[str, Any]) -> int:
    try:
        value = int(float(row.get("ranking_limit") or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def ranking_rank(row: dict[str, Any]) -> int:
    try:
        value = int(float(row.get("ranking_rank") or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _optional_number(row: dict[str, Any], field: str) -> float | None:
    try:
        value = float(row.get(field))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def ranking_score(row: dict[str, Any]) -> tuple[float, float, float]:
    final_score = _optional_number(row, "match_score_final")
    initial_score = _optional_number(row, "match_score_initial")
    confidence = _optional_number(row, "fit_confidence") or 0.0
    effective_score = final_score if final_score is not None else initial_score or 0.0
    return effective_score, confidence, initial_score or 0.0


def validate_ranking_pending_update(row: dict[str, Any]) -> str:
    if normalized_apply_state(row) != RANKING_PENDING:
        return ""
    if str(row.get("decision_status") or "").strip().lower() != "recommended_apply":
        return "ranking_pending requires decision_status=recommended_apply"
    if str(row.get("application_status") or "").strip():
        return "ranking_pending must not set application_status before a real application outcome"
    if ranking_limit(row) <= 0:
        return "ranking_pending requires a positive ranking_limit"
    if _optional_number(row, "match_score_final") is None and _optional_number(row, "match_score_initial") is None:
        return "ranking_pending requires a numeric match_score_final or match_score_initial"
    return ""


def ranked_queue_updates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, row in enumerate(rows):
        if normalized_apply_state(row) not in RANKED_REVIEW_STATES:
            continue
        group = str(row.get("ranking_group") or "default").strip() or "default"
        groups[group].append((index, row))

    updates: list[dict[str, Any]] = []
    for group, members in groups.items():
        states = {normalized_apply_state(row) for _, row in members}
        if RANKING_PENDING not in states and states.issubset({READY_TO_APPLY, DEFERRED_BY_RANK}):
            continue
        limits = {ranking_limit(row) for _, row in members if ranking_limit(row) > 0}
        if not limits:
            continue
        if len(limits) != 1:
            raise ValueError(f"ranking_limit conflict for group={group}: {sorted(limits)}")
        limit = next(iter(limits))
        ordered = sorted(
            members,
            key=lambda item: (
                -ranking_score(item[1])[0],
                -ranking_score(item[1])[1],
                -ranking_score(item[1])[2],
                item[0],
                str(item[1].get("job_id") or ""),
            ),
        )
        for rank, (_, row) in enumerate(ordered, start=1):
            job_id = str(row.get("job_id") or "").strip()
            if not job_id:
                continue
            expected_state = READY_TO_APPLY if rank <= limit else DEFERRED_BY_RANK
            if (
                normalized_apply_state(row) == expected_state
                and ranking_limit(row) == limit
                and ranking_rank(row) == rank
                and str(row.get("ranking_group") or "default").strip() == group
            ):
                continue
            updates.append(
                {
                    "job_id": job_id,
                    "apply_state": expected_state,
                    "ranking_group": group,
                    "ranking_limit": limit,
                    "ranking_rank": rank,
                }
            )
    return updates


def ranked_state_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {RANKING_PENDING: 0, READY_TO_APPLY: 0, DEFERRED_BY_RANK: 0}
    for row in rows:
        state = normalized_apply_state(row)
        if state in counts:
            counts[state] += 1
    return counts
