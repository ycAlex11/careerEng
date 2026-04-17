"""Resume Markdown to Typst/PDF export helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess

from careereng.utils import ensure_dir, safe_file_stem


DEFAULT_RESUME_TEMPLATE_NAME = "default.typ"
DEFAULT_APPLY_RESUME_PDF_NAME = "cv.pdf"
DEFAULT_RESUME_TEMPLATE_TEXT = """// CareerEng default resume template.
// Edit this file inside workspace/cv/templates/ to change page layout or styling.

#set page(paper: "a4", margin: (x: 1.4cm, y: 1.2cm))
#set text(
  font: ("Helvetica Neue", "Arial", "PingFang SC", "Noto Sans CJK SC", "Liberation Sans"),
  size: 10pt,
)
#set par(leading: 0.65em)

{{ content }}
"""


class ResumeExportError(RuntimeError):
    """Raised when resume export cannot continue."""


@dataclass(frozen=True)
class ResumeExportResult:
    template_path: Path
    typ_path: Path
    pdf_path: Path


def default_resume_template_text() -> str:
    return DEFAULT_RESUME_TEMPLATE_TEXT.rstrip() + "\n"


def ensure_resume_assets(workspace: Path) -> dict[str, Path]:
    cv_dir = ensure_dir(workspace / "cv")
    templates_dir = ensure_dir(cv_dir / "templates")
    exports_dir = ensure_dir(cv_dir / "exports")
    variants_dir = ensure_dir(cv_dir / "variants")
    template_path = templates_dir / DEFAULT_RESUME_TEMPLATE_NAME
    if not template_path.exists():
        template_path.write_text(default_resume_template_text(), encoding="utf-8")
    return {
        "cv_dir": cv_dir,
        "templates_dir": templates_dir,
        "exports_dir": exports_dir,
        "variants_dir": variants_dir,
        "template_path": template_path,
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return path.read_bytes().decode("utf-8", errors="ignore")


def _escape_typst_text(text: str) -> str:
    escaped = text.replace("\\", "\\\\")
    for old, new in (
        ("#", "\\#"),
        ("[", "\\["),
        ("]", "\\]"),
        ("$", "\\$"),
        ("@", "\\@"),
        ("*", "\\*"),
        ("_", "\\_"),
    ):
        escaped = escaped.replace(old, new)
    return escaped


def _escape_typst_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


_INLINE_TOKEN_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|__([^_]+)__")
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
_DATE_RANGE_RE = re.compile(
    r"^\d{4}(?:[./-]\d{1,2})?\s*[-–—]\s*(?:\d{4}(?:[./-]\d{1,2})?|now|present)"
    r"(?:\s*,\s*\d{4}(?:[./-]\d{1,2})?\s*[-–—]\s*(?:\d{4}(?:[./-]\d{1,2})?|now|present))*$",
    re.IGNORECASE,
)


def _markdown_inline_to_typst(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _INLINE_TOKEN_RE.finditer(text):
        if match.start() > cursor:
            parts.append(_escape_typst_text(text[cursor : match.start()]))
        link_label = match.group(1)
        link_url = match.group(2)
        strong_body = match.group(3) or match.group(4)
        if link_label is not None and link_url is not None:
            label = _markdown_inline_to_typst(link_label.strip())
            url = _escape_typst_string(link_url.strip())
            parts.append(f'#link("{url}")[{label}]')
        elif strong_body is not None:
            parts.append(f'*{_markdown_inline_to_typst(strong_body.strip())}*')
        cursor = match.end()
    if cursor < len(text):
        parts.append(_escape_typst_text(text[cursor:]))
    return "".join(parts)


def _normalize_block_text(text: str) -> str:
    normalized = str(text or "").strip()
    normalized = re.sub(r"^#{1,6}\s+", "", normalized)
    strong_only = re.fullmatch(r"\*\*(.+?)\*\*|__(.+?)__", normalized)
    if strong_only:
        normalized = strong_only.group(1) or strong_only.group(2) or ""
    return normalized.strip()


def _is_table_row(text: str) -> bool:
    stripped = str(text or "").strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _split_table_cells(text: str) -> list[str]:
    stripped = str(text or "").strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_separator_row(text: str) -> bool:
    cells = _split_table_cells(text)
    return bool(cells) and all(_TABLE_SEPARATOR_CELL_RE.fullmatch(cell or "") for cell in cells)


def _looks_like_date_range(text: str) -> bool:
    return bool(_DATE_RANGE_RE.fullmatch(_normalize_block_text(text).replace(" ", "")))


def _looks_like_compact_role_line(text: str) -> bool:
    normalized = _normalize_block_text(text)
    if not normalized or len(normalized) > 60:
        return False
    if _looks_like_date_range(normalized):
        return False
    if "|" in normalized or "," in normalized:
        return False
    word_count = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9+/#.&()'\\-]*", normalized))
    return 1 <= word_count <= 6


def _compact_entry_piece(text: str) -> str:
    return re.sub(r"^#{1,6}\s+", "", str(text or "").strip()).strip()


def _next_nonempty_index(lines: list[str], start: int) -> int | None:
    index = start
    while index < len(lines):
        if lines[index].strip():
            return index
        index += 1
    return None


def convert_markdown_to_typst(markdown_text: str, *, fallback_title: str = "Resume") -> str:
    normalized = str(markdown_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    output: list[str] = []
    saw_title = False

    def flush_paragraph(buffer: list[str]) -> None:
        if not buffer:
            return
        paragraph = " ".join(part.strip() for part in buffer if part.strip())
        if paragraph:
            output.append(_markdown_inline_to_typst(paragraph))
            output.append("")
        buffer.clear()

    paragraph_buffer: list[str] = []
    heading_re = re.compile(r"^(#{1,3})\s+(.*)$")
    bullet_re = re.compile(r"^(\s*)[-*]\s+(.*)$")
    enum_re = re.compile(r"^(\s*)\d+\.\s+(.*)$")

    index = 0
    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()

        if _is_table_row(raw_line):
            flush_paragraph(paragraph_buffer)
            while index < len(lines) and _is_table_row(lines[index]):
                table_line = lines[index]
                if not _is_table_separator_row(table_line):
                    row_text = " | ".join(cell for cell in _split_table_cells(table_line) if cell)
                    if row_text:
                        output.append(_markdown_inline_to_typst(row_text))
                        output.append("")
                index += 1
            continue

        next_index = _next_nonempty_index(lines, index + 1)
        if stripped and next_index is not None:
            third_index = _next_nonempty_index(lines, next_index + 1)
            if third_index is not None:
                if (
                    not bullet_re.match(raw_line)
                    and not enum_re.match(raw_line)
                    and not _looks_like_date_range(raw_line)
                    and _looks_like_compact_role_line(lines[next_index])
                    and _looks_like_date_range(lines[third_index])
                ):
                    flush_paragraph(paragraph_buffer)
                    compact_line = " | ".join(
                        (
                            _compact_entry_piece(raw_line),
                            _compact_entry_piece(lines[next_index]),
                            _compact_entry_piece(lines[third_index]),
                        )
                    )
                    output.append(_markdown_inline_to_typst(compact_line))
                    output.append("")
                    heading = heading_re.match(raw_line)
                    if heading and len(heading.group(1)) == 1:
                        saw_title = True
                    index = third_index + 1
                    continue

        heading = heading_re.match(raw_line)
        if heading:
            flush_paragraph(paragraph_buffer)
            level = min(3, len(heading.group(1)))
            body = heading.group(2).strip()
            if level == 1:
                saw_title = True
            output.append(f'{"=" * level} {_markdown_inline_to_typst(body)}')
            output.append("")
            index += 1
            continue

        bullet = bullet_re.match(raw_line)
        if bullet:
            flush_paragraph(paragraph_buffer)
            indent = "  " * (len(bullet.group(1).replace("\t", "  ")) // 2)
            output.append(f"{indent}- {_markdown_inline_to_typst(bullet.group(2).strip())}")
            index += 1
            continue

        enum_item = enum_re.match(raw_line)
        if enum_item:
            flush_paragraph(paragraph_buffer)
            indent = "  " * (len(enum_item.group(1).replace("\t", "  ")) // 2)
            output.append(f"{indent}+ {_markdown_inline_to_typst(enum_item.group(2).strip())}")
            index += 1
            continue

        if not stripped:
            flush_paragraph(paragraph_buffer)
            if output and output[-1] != "":
                output.append("")
            index += 1
            continue

        paragraph_buffer.append(stripped)
        index += 1

    flush_paragraph(paragraph_buffer)

    while output and output[-1] == "":
        output.pop()

    if not saw_title:
        title = _markdown_inline_to_typst(fallback_title.strip() or "Resume")
        output = [f"= {title}", ""] + output

    return "\n".join(output).rstrip() + "\n"


def render_typst_document(*, template_text: str, content_text: str) -> str:
    placeholder = "{{ content }}"
    if placeholder not in template_text:
        raise ResumeExportError(f"template missing required placeholder: {placeholder}")
    return template_text.replace(placeholder, content_text.rstrip() + "\n").rstrip() + "\n"


def compile_typst(*, typ_path: Path, pdf_path: Path) -> None:
    binary = shutil.which("typst")
    if not binary:
        raise ResumeExportError("`typst` is not installed or not on PATH.")
    ensure_dir(pdf_path.parent)
    proc = subprocess.run(
        [binary, "compile", str(typ_path), str(pdf_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ResumeExportError(f"typst compile failed: {detail or f'exit code {proc.returncode}'}")


def _resolve_template_path(*, workspace: Path, template: str) -> Path:
    assets = ensure_resume_assets(workspace)
    if not str(template or "").strip():
        return assets["template_path"]
    candidate = Path(template).expanduser()
    if candidate.is_absolute():
        return candidate
    if candidate.exists():
        return candidate.resolve()
    workspace_candidate = assets["templates_dir"] / candidate
    if workspace_candidate.exists():
        return workspace_candidate.resolve()
    return candidate.resolve()


def default_apply_resume_pdf_path(workspace: Path) -> Path:
    assets = ensure_resume_assets(workspace)
    return (assets["exports_dir"] / DEFAULT_APPLY_RESUME_PDF_NAME).resolve()


def _default_resume_source_path(workspace: Path) -> Path | None:
    current_dir = ensure_dir(Path(workspace) / "cv" / "current")
    preferred = current_dir / "cv.md"
    if preferred.exists() and preferred.is_file():
        return preferred.resolve()
    candidates = [
        path
        for path in sorted(current_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
        if path.is_file() and path.name != "metadata.json"
    ]
    return candidates[0].resolve() if candidates else None


def ensure_default_resume_pdf(workspace: Path) -> Path:
    pdf_path = default_apply_resume_pdf_path(workspace)
    if pdf_path.exists() and pdf_path.is_file():
        return pdf_path

    source_path = _default_resume_source_path(workspace)
    if source_path is None:
        raise ResumeExportError("no current resume source found under workspace/cv/current")

    if source_path.suffix.lower() == ".pdf":
        ensure_dir(pdf_path.parent)
        shutil.copy2(source_path, pdf_path)
        return pdf_path.resolve()

    result = export_resume_pdf(
        workspace=workspace,
        markdown_path=source_path,
        output_path=pdf_path,
    )
    return result.pdf_path.resolve()


def export_resume_pdf(
    *,
    workspace: Path,
    markdown_path: Path,
    output_path: Path | None = None,
    template: str = "",
) -> ResumeExportResult:
    source_path = Path(markdown_path).expanduser()
    if not source_path.exists() or not source_path.is_file():
        raise ResumeExportError(f"resume source not found: {source_path}")

    assets = ensure_resume_assets(workspace)
    template_path = _resolve_template_path(workspace=workspace, template=template)
    if not template_path.exists() or not template_path.is_file():
        raise ResumeExportError(f"template not found: {template_path}")

    stem = safe_file_stem(source_path.stem or "resume") or "resume"
    if output_path is None:
        pdf_path = assets["exports_dir"] / f"{stem}.pdf"
    else:
        pdf_path = Path(output_path).expanduser()
        if not pdf_path.is_absolute():
            pdf_path = pdf_path.resolve()
    pdf_path = pdf_path.with_suffix(".pdf")
    typ_path = pdf_path.with_suffix(".typ")

    markdown_text = _read_text(source_path)
    typst_content = convert_markdown_to_typst(markdown_text, fallback_title=source_path.stem or "Resume")
    template_text = _read_text(template_path)
    rendered_typst = render_typst_document(template_text=template_text, content_text=typst_content)

    ensure_dir(typ_path.parent)
    typ_path.write_text(rendered_typst, encoding="utf-8")
    compile_typst(typ_path=typ_path, pdf_path=pdf_path)

    return ResumeExportResult(
        template_path=template_path.resolve(),
        typ_path=typ_path.resolve(),
        pdf_path=pdf_path.resolve(),
    )
