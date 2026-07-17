"""Small Markdown schema helpers for skill-driven job workflows."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SITE_POLICY_SECTION = "Site Policy"
MATCHING_POLICY_SECTION = "Matching Policy"

GLOBAL_SECTION_INJECTIONS: dict[str, set[str]] = {
    SITE_POLICY_SECTION: {"job_filtering", "job_retrieval", "apply", "application_status_review"},
    MATCHING_POLICY_SECTION: {"job_filtering", "apply"},
}

SECTION_ALIASES = {
    "apply_workflow": "apply",
}


def normalize_section_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return slug or "section"


def canonical_section_title(value: str) -> str:
    title = str(value or "").strip()
    slug = normalize_section_slug(title)
    if slug in SECTION_ALIASES:
        return SECTION_ALIASES[slug].replace("_", " ").title()
    return title


def extract_markdown_sections(markdown: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_title = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_lines
        if current_title:
            sections[current_title] = "\n".join(current_lines).strip()
        current_lines = []

    for raw_line in str(markdown or "").splitlines():
        if raw_line.startswith("## "):
            flush()
            current_title = raw_line[3:].strip()
            continue
        if current_title:
            current_lines.append(raw_line)
    flush()
    return sections


def section_text(markdown: str, title: str) -> str:
    target_slug = normalize_section_slug(title)
    for section_title, body in extract_markdown_sections(markdown).items():
        if normalize_section_slug(section_title) == target_slug:
            return str(body or "").strip()
    return ""


def hash_text(value: str) -> str:
    normalized = "\n".join(line.rstrip() for line in str(value or "").strip().splitlines()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def section_hash(markdown: str, title: str) -> str:
    return hash_text(section_text(markdown, title))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(str(path).encode("utf-8"))
    digest.update(b"\0")
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        data = b"<missing>"
    except Exception:
        data = b"<unreadable>"
    digest.update(data)
    return digest.hexdigest()


def context_hash(payload: dict[str, Any]) -> str:
    data = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
