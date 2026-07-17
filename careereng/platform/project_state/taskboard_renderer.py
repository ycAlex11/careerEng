"""Markdown rendering helpers for taskboards."""

from __future__ import annotations


def render_taskboard(
    *,
    taskboard_id: str,
    created_at: str,
    updated_at: str,
    source_name: str,
    body: str,
) -> str:
    source_line = f"- Source: `{source_name}`\n" if source_name else ""
    cleaned_body = body.strip()
    return (
        "# Current Taskboard\n\n"
        f"- Taskboard ID: `{taskboard_id}`\n"
        "- Status: `active`\n"
        f"- Created At: `{created_at}`\n"
        f"- Updated At: `{updated_at}`\n"
        f"{source_line}"
        "\n"
        "## Tasks\n\n"
        f"{cleaned_body}\n"
    )


def render_initial_taskboard(*, taskboard_id: str, created_at: str, source_name: str, body: str) -> str:
    return render_taskboard(
        taskboard_id=taskboard_id,
        created_at=created_at,
        updated_at=created_at,
        source_name=source_name,
        body=body,
    )


def render_update_section(*, updated_at: str, source_name: str, body: str) -> str:
    """Render the legacy update fragment without changing store semantics.

    New taskboard updates replace ``current.md`` and snapshot the prior board.
    This helper remains only for compatibility with legacy renderer imports.
    """
    source_line = f"\nSource: `{source_name}`\n" if source_name else ""
    return f"\n\n## Update {updated_at}\n{source_line}\n{body.strip()}\n"


def render_no_taskboard() -> str:
    return "No current taskboard found."
