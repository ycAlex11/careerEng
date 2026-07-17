"""Phase extraction and prompt composition for browser automation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from careereng.career.applications.skill_policy import GLOBAL_SECTION_INJECTIONS


@dataclass(frozen=True)
class PhasePrompt:
    title: str
    slug: str
    project_text: str
    site_text: str
    ignore_phrases: tuple[str, ...] = ()

    @property
    def combined_guidance(self) -> str:
        parts: list[str] = []
        if self.site_text:
            parts.append("Site skill guidance:\n" + self.site_text.strip())
        if self.project_text:
            parts.append("Project skill guidance:\n" + self.project_text.strip())
        return "\n\n".join(part for part in parts if part.strip()).strip()


def load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def normalize_phase_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    if slug == "apply_workflow":
        return "apply"
    return slug or "phase"


def _dedupe_phrases(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    items: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(text)
    return tuple(items)


def extract_browser_context_ignore(text: str) -> tuple[str, tuple[str, ...]]:
    body_lines: list[str] = []
    ignore_phrases: list[str] = []
    in_ignore_block = False

    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if stripped == "### Browser Context Ignore":
            in_ignore_block = True
            continue
        if in_ignore_block and stripped.startswith("### "):
            in_ignore_block = False
        if in_ignore_block:
            match = re.match(r"^\s*[-*]\s+(.*\S)\s*$", raw_line)
            if match:
                ignore_phrases.append(match.group(1).strip())
            elif stripped:
                ignore_phrases.append(stripped)
            continue
        body_lines.append(raw_line)

    body = "\n".join(body_lines).strip()
    return body, _dedupe_phrases(ignore_phrases)


def extract_phase_sections(markdown: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_title = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_lines
        if not current_title:
            current_lines = []
            return
        text = "\n".join(current_lines).strip()
        sections[current_title] = text
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


def _global_section_texts(sections: dict[str, str], slug: str) -> list[str]:
    parts: list[str] = []
    for title, target_slugs in GLOBAL_SECTION_INJECTIONS.items():
        if slug not in target_slugs:
            continue
        text = str(sections.get(title) or "").strip()
        if text:
            parts.append(f"### {title} (global)\n\n{text}")
    return parts


def _with_global_sections(section_text: str, global_sections: list[str]) -> str:
    parts = [str(section_text or "").strip(), *[part.strip() for part in global_sections if part.strip()]]
    return "\n\n".join(part for part in parts if part).strip()


def build_phase_prompts(
    project_markdown: str,
    site_markdown: str,
    *,
    allowed_slugs: set[str] | None = None,
) -> list[PhasePrompt]:
    project_sections_raw = extract_phase_sections(project_markdown)
    site_sections_raw = extract_phase_sections(site_markdown)
    project_sections: dict[str, str] = {}
    project_ignore_by_slug: dict[str, tuple[str, ...]] = {}
    for title, text in project_sections_raw.items():
        cleaned, ignore_phrases = extract_browser_context_ignore(text)
        project_sections[title] = cleaned
        project_ignore_by_slug[normalize_phase_name(title)] = ignore_phrases
    site_sections: dict[str, str] = {}
    site_ignore_by_slug: dict[str, tuple[str, ...]] = {}
    for title, text in site_sections_raw.items():
        cleaned, ignore_phrases = extract_browser_context_ignore(text)
        site_sections[title] = cleaned
        site_ignore_by_slug[normalize_phase_name(title)] = ignore_phrases
    site_by_slug = {normalize_phase_name(title): text for title, text in site_sections.items()}
    project_titles_by_slug = {normalize_phase_name(title): title for title in project_sections.keys()}
    global_section_slugs = {normalize_phase_name(title) for title in GLOBAL_SECTION_INJECTIONS.keys()}

    prompts: list[PhasePrompt] = []
    seen: set[str] = set()

    for title, project_text in project_sections.items():
        slug = normalize_phase_name(title)
        if slug in global_section_slugs:
            continue
        if allowed_slugs and slug not in allowed_slugs:
            continue
        prompts.append(
            PhasePrompt(
                title=project_titles_by_slug.get(slug, title),
                slug=slug,
                project_text=_with_global_sections(project_text, _global_section_texts(project_sections, slug)),
                site_text=_with_global_sections(site_by_slug.get(slug, ""), _global_section_texts(site_sections, slug)),
                ignore_phrases=_dedupe_phrases(
                    list(project_ignore_by_slug.get(slug, ())) + list(site_ignore_by_slug.get(slug, ()))
                ),
            )
        )
        seen.add(slug)

    for title, site_text in site_sections.items():
        slug = normalize_phase_name(title)
        if slug in global_section_slugs:
            continue
        if slug in seen:
            continue
        if allowed_slugs and slug not in allowed_slugs:
            continue
        prompts.append(
            PhasePrompt(
                title=title,
                slug=slug,
                project_text=_with_global_sections("", _global_section_texts(project_sections, slug)),
                site_text=_with_global_sections(site_text, _global_section_texts(site_sections, slug)),
                ignore_phrases=site_ignore_by_slug.get(slug, ()),
            )
        )

    return prompts
