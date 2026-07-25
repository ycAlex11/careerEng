"""Declarative site execution modes shared by site and loop orchestration.

Modes describe the maturity of a site's declarative Skill. They never encode
site workflow, matching, or browser decisions. ``apply_enabled`` remains a
separate user authorization flag.
"""

from __future__ import annotations


SITE_MODE_DRAFT = "draft"
SITE_MODE_EXPLORATION = "exploration"
SITE_MODE_READY = "ready"
SITE_MODES = frozenset({SITE_MODE_DRAFT, SITE_MODE_EXPLORATION, SITE_MODE_READY})

EXECUTION_BLOCKED = "blocked_initialization"
EXECUTION_EXPLORATION = "exploration"
EXECUTION_STABLE = "stable"


def normalize_site_mode(value: object, *, default: str = SITE_MODE_DRAFT) -> str:
    """Return a supported declared mode without inferring site semantics."""

    normalized = str(value or "").strip().lower()
    return normalized if normalized in SITE_MODES else default


def execution_mode_for_site_mode(mode: object) -> str:
    """Map a declared mode to the generic orchestration route."""

    normalized = normalize_site_mode(mode)
    if normalized == SITE_MODE_EXPLORATION:
        return EXECUTION_EXPLORATION
    if normalized == SITE_MODE_READY:
        return EXECUTION_STABLE
    return EXECUTION_BLOCKED


def site_mode_is_runnable(mode: object) -> bool:
    return execution_mode_for_site_mode(mode) != EXECUTION_BLOCKED
