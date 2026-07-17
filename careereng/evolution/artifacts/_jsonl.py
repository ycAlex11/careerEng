"""Shared JSONL upsert mechanics for evolution-owned stores."""

from __future__ import annotations

from typing import Any

from careereng.platform.persistence import JSONLStore


def upsert_rows(store: JSONLStore, rows: list[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
    existing = store.read_all()
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in existing:
        row_key = str(row.get(key) or "").strip()
        if not row_key:
            continue
        if row_key not in by_key:
            order.append(row_key)
        by_key[row_key] = row
    for row in rows:
        row_key = str(row.get(key) or "").strip()
        if not row_key:
            continue
        current = by_key.get(row_key)
        if current:
            merged = {**current, **row}
            if current.get("created_at"):
                merged["created_at"] = current["created_at"]
            by_key[row_key] = merged
        else:
            order.append(row_key)
            by_key[row_key] = row
    merged_rows = [by_key[item] for item in order if item in by_key]
    store.write_all(merged_rows)
    return merged_rows
