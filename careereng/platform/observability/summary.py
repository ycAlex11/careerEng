"""Aggregate LLM usage metrics."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from careereng.platform.persistence import JSONLStore
from careereng.platform.reporting import ReportArtifactStore
from careereng.utils import ensure_dir, now_iso, safe_file_stem


GROUP_KEYS = ("site_key", "phase", "api_type", "status", "model")


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _metrics_path(workspace: Path) -> Path:
    return workspace / "metrics" / "llm_usage.jsonl"


def _performance_path(workspace: Path) -> Path:
    return workspace / "metrics" / "performance_events.jsonl"


def _load_rows(workspace: Path) -> list[dict[str, Any]]:
    path = _metrics_path(workspace)
    if not path.exists():
        return []
    return JSONLStore(path).read_all()


def _load_performance_rows(workspace: Path) -> list[dict[str, Any]]:
    path = _performance_path(workspace)
    if not path.exists():
        return []
    return JSONLStore(path).read_all()


def _latest_batch_id(rows: list[dict[str, Any]]) -> str:
    """Return the latest batch observed by the metrics source itself."""

    for row in reversed(rows):
        batch_id = str(row.get("batch_id") or "").strip()
        if batch_id:
            return batch_id
    return ""


def _filter_rows(
    rows: list[dict[str, Any]],
    *,
    batch_id: str = "",
    site_key: str = "",
    phase: str = "",
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if batch_id and str(row.get("batch_id") or "") != batch_id:
            continue
        if site_key and str(row.get("site_key") or row.get("site_id") or "") != site_key:
            continue
        if phase and str(row.get("phase") or "") != phase:
            continue
        filtered.append(row)
    return filtered


def _empty_totals() -> dict[str, Any]:
    return {
        "calls": 0,
        "ok_calls": 0,
        "error_calls": 0,
        "elapsed_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "unknown_token_calls": 0,
    }


def _empty_performance_totals() -> dict[str, Any]:
    return {
        "events": 0,
        "elapsed_ms": 0,
        "browser_tool_calls": 0,
        "state_tool_calls": 0,
        "retry_count": 0,
        "snapshot_count": 0,
        "compact_observation_count": 0,
        "full_observation_count": 0,
        "agent_input_bytes": 0,
        "technical_error_count": 0,
        "cache_lookups": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_reads": 0,
        "cache_proposals": 0,
        "cache_validations": 0,
        "cache_stale_or_retired": 0,
        "browser_sequences": 0,
        "browser_sequence_steps": 0,
    }


def _accumulate_performance(totals: dict[str, Any], row: dict[str, Any]) -> None:
    totals["events"] += 1
    totals["elapsed_ms"] += _int_value(row.get("elapsed_ms"))
    operation = str(row.get("operation") or "")
    tool_name = str(row.get("tool_name") or "")
    if operation == "browser_tool":
        totals["browser_tool_calls"] += 1
    if operation == "state_tool":
        totals["state_tool_calls"] += 1
    if operation == "browser_sequence":
        totals["browser_sequences"] += 1
        totals["browser_sequence_steps"] += _int_value(row.get("sequence_step_count"))
    if operation == "cache":
        action = str(row.get("cache_action") or "")
        if action == "lookup":
            totals["cache_lookups"] += 1
        elif action == "hit":
            totals["cache_hits"] += 1
        elif action == "miss":
            totals["cache_misses"] += 1
        elif action == "read":
            totals["cache_reads"] += 1
        elif action == "proposed":
            totals["cache_proposals"] += 1
        elif action == "validated":
            totals["cache_validations"] += 1
            status = str(row.get("cache_validation_status") or "")
            if status in {"stale", "retired"}:
                totals["cache_stale_or_retired"] += 1
    if bool(row.get("retry")):
        totals["retry_count"] += 1
    if tool_name == "browser_snapshot" or str(row.get("observation_kind") or "") == "snapshot":
        totals["snapshot_count"] += 1
    if str(row.get("observation_kind") or "") == "compact":
        totals["compact_observation_count"] += 1
    if str(row.get("observation_kind") or "") == "full":
        totals["full_observation_count"] += 1
    totals["agent_input_bytes"] += _int_value(row.get("agent_input_bytes"))
    if str(row.get("status") or "") == "error":
        totals["technical_error_count"] += 1


def _accumulate(totals: dict[str, Any], row: dict[str, Any]) -> None:
    totals["calls"] += 1
    if str(row.get("status") or "") == "ok":
        totals["ok_calls"] += 1
    else:
        totals["error_calls"] += 1
    totals["elapsed_ms"] += _int_value(row.get("elapsed_ms"))
    total_tokens = row.get("total_tokens")
    if total_tokens is None:
        totals["unknown_token_calls"] += 1
    else:
        totals["total_tokens"] += _int_value(total_tokens)
    totals["input_tokens"] += _int_value(row.get("input_tokens"))
    totals["output_tokens"] += _int_value(row.get("output_tokens"))


def _group_summary(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        if not value.strip():
            value = "unknown"
        bucket = grouped.setdefault(value, {"name": value, **_empty_totals()})
        _accumulate(bucket, row)
    return sorted(
        grouped.values(),
        key=lambda item: (_int_value(item.get("total_tokens")), _int_value(item.get("elapsed_ms")), str(item.get("name"))),
        reverse=True,
    )


def build_metrics_summary(
    *,
    workspace: Path | str,
    batch_id: str = "",
    site_key: str = "",
    phase: str = "",
) -> dict[str, Any]:
    workspace_path = Path(workspace)
    rows = _load_rows(workspace_path)
    performance_rows = _load_performance_rows(workspace_path)
    requested_batch = str(batch_id or "").strip()
    resolved_batch = _latest_batch_id(rows + performance_rows) if requested_batch == "latest" else requested_batch
    filtered_rows = _filter_rows(
        rows,
        batch_id=resolved_batch,
        site_key=str(site_key or "").strip(),
        phase=str(phase or "").strip(),
    )
    filtered_performance_rows = _filter_rows(
        performance_rows,
        batch_id=resolved_batch,
        site_key=str(site_key or "").strip(),
        phase=str(phase or "").strip(),
    )
    totals = _empty_totals()
    providers: Counter[str] = Counter()
    models: Counter[str] = Counter()
    api_types: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    for row in filtered_rows:
        _accumulate(totals, row)
        providers[str(row.get("provider") or "unknown")] += 1
        models[str(row.get("model") or "unknown")] += 1
        api_types[str(row.get("api_type") or "unknown")] += 1
        statuses[str(row.get("status") or "unknown")] += 1
    performance_totals = _empty_performance_totals()
    for row in filtered_performance_rows:
        _accumulate_performance(performance_totals, row)
    return {
        "generated_at": now_iso(),
        "source_path": str(_metrics_path(workspace_path)),
        "performance_source_path": str(_performance_path(workspace_path)),
        "filters": {
            "batch_id": resolved_batch,
            "site_key": str(site_key or "").strip(),
            "phase": str(phase or "").strip(),
        },
        "totals": totals,
        "providers": dict(providers),
        "models": dict(models),
        "api_types": dict(api_types),
        "statuses": dict(statuses),
        "groups": {key: _group_summary(filtered_rows, key) for key in GROUP_KEYS},
        "performance": {
            "totals": performance_totals,
            "events": filtered_performance_rows,
        },
        "error_rows": [
            {
                "ts": str(row.get("ts") or ""),
                "batch_id": str(row.get("batch_id") or ""),
                "site_key": str(row.get("site_key") or ""),
                "phase": str(row.get("phase") or ""),
                "status": str(row.get("status") or ""),
                "error_type": str(row.get("error_type") or ""),
                "elapsed_ms": _int_value(row.get("elapsed_ms")),
            }
            for row in filtered_rows
            if str(row.get("status") or "") != "ok"
        ],
    }


def save_metrics_summary(summary: dict[str, Any], *, workspace: Path | str) -> Path:
    workspace_path = Path(workspace)
    filters = summary.get("filters") if isinstance(summary.get("filters"), dict) else {}
    batch_id = str(filters.get("batch_id") or "").strip()
    name = safe_file_stem(batch_id) if batch_id else f"summary_{safe_file_stem(str(summary.get('generated_at') or now_iso()))}"
    path = ensure_dir(workspace_path / "metrics" / "summaries") / f"{name}.json"
    ReportArtifactStore(workspace_path).write_json(
        artifact_id=f"platform_metrics_summary:{name}",
        domain="platform_observability",
        report_type="metrics_summary",
        json_path=path,
        payload=summary,
        metadata={"batch_id": batch_id},
    )
    return path
