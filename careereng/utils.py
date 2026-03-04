"""Utility helpers."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    yaml_text = text[4:end]
    body = text[end + 5 :]
    parsed = yaml.safe_load(yaml_text) or {}
    if not isinstance(parsed, dict):
        parsed = {}
    return parsed, body


def dump_front_matter(data: dict[str, Any], body: str = "") -> str:
    head = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip()
    if body and not body.endswith("\n"):
        body += "\n"
    return f"---\n{head}\n---\n{body}"


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def safe_file_stem(value: str) -> str:
    stem = value.strip().lower().replace(" ", "-")
    stem = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", stem)
    stem = re.sub(r"-+", "-", stem).strip("-")
    return stem or "site"
