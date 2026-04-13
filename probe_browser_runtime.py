#!/usr/bin/env python3
"""Probe browser runtime Responses behavior without running the full job flow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import anyio

from careereng.browser_controls.prompting import build_phase_prompts, load_text
from careereng.browser_controls.runtime import BrowserPhaseRuntime, BrowserRuntimeConfig, ResponsesClient
from careereng.config.loader import load_auth, load_config


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _latest_snapshot_file(project_root: Path, site_key: str) -> Path | None:
    base = project_root / "workspace" / "tmp" / "browser_controls" / site_key
    if not base.exists():
        return None
    candidates = sorted(base.glob("**/session-*/session.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0]
    page_candidates = sorted(base.glob("**/page-*.yml"), key=lambda path: path.stat().st_mtime, reverse=True)
    if page_candidates:
        return page_candidates[0]
    return None


def _site_skill_path(project_root: Path, site_key: str) -> Path:
    return project_root / "workspace" / "sites" / site_key / "skills" / "SKILL.md"


def _project_skill_path(project_root: Path) -> Path:
    return project_root / "skills" / "search" / "jobs" / "SKILL.md"


def _build_runtime(project_root: Path) -> BrowserPhaseRuntime:
    config = load_config(project_root)
    auth = load_auth(project_root)
    return BrowserPhaseRuntime(
        BrowserRuntimeConfig(
            api_base=str(getattr(config.browser, "api_base", "") or config.providers.openai.api_base).rstrip("/"),
            api_key=str(auth.openai_api_key or ""),
            model=str(getattr(config.browser, "model", "") or config.agent.default_model or "gpt-5"),
            reasoning_effort=str(getattr(config.browser, "reasoning_effort", "") or "high"),
            phase_timeout_seconds=int(getattr(config.browser, "phase_timeout_seconds", 180) or 180),
            step_timeout_seconds=int(getattr(config.browser, "step_timeout_seconds", 30) or 30),
            max_step_retries=int(getattr(config.browser, "max_step_retries", 1) or 1),
            max_phase_steps=int(getattr(config.browser, "max_phase_steps", 24) or 24),
        )
    )


def _phase_prompt(project_root: Path, site_key: str, phase_slug: str):
    prompts = build_phase_prompts(
        load_text(_project_skill_path(project_root)),
        load_text(_site_skill_path(project_root, site_key)),
        allowed_slugs={phase_slug},
    )
    if not prompts:
        raise SystemExit(f"phase not found: {phase_slug}")
    return prompts[0]


def _site_name(site_key: str) -> str:
    if not site_key:
        return "site"
    return site_key[:1].upper() + site_key[1:]


def _dummy_browser_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "browser_click",
            "description": "Click a visible browser element.",
            "parameters": {
                "type": "object",
                "properties": {
                    "element": {"type": "string"},
                    "ref": {"type": "string"},
                },
                "required": ["element", "ref"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "browser_snapshot",
            "description": "Capture a fresh browser snapshot.",
            "parameters": {
                "type": "object",
                "properties": {"filename": {"type": "string"}},
                "required": ["filename"],
                "additionalProperties": False,
            },
        },
    ]


async def _run_probe(args: argparse.Namespace) -> int:
    project_root = _project_root()
    runtime = _build_runtime(project_root)
    phase = _phase_prompt(project_root, args.site, args.phase)
    snapshot_path = Path(args.snapshot_file).expanduser() if args.snapshot_file else _latest_snapshot_file(project_root, args.site)
    if snapshot_path is None or not snapshot_path.exists():
        raise SystemExit(f"snapshot file not found for site: {args.site}")
    snapshot_text = snapshot_path.read_text(encoding="utf-8")

    tools = [BrowserPhaseRuntime.phase_result_tool()]
    if args.tool_set == "phase_and_dummy_browser":
        tools = _dummy_browser_tools() + tools

    payload = runtime._payload(
        input_items=[
            {"role": "system", "content": runtime._system_prompt(site_name=_site_name(args.site), phase=phase)},
            {
                "role": "user",
                "content": runtime._user_prompt(
                    site_name=_site_name(args.site),
                    entry_url=args.entry_url,
                    phase=phase,
                ),
            },
            {"role": "user", "content": snapshot_text[: args.max_snapshot_chars]},
        ],
        tools=tools,
    )

    response = await runtime.responses.create(payload)
    output = response.get("output") if isinstance(response, dict) else []
    output_items = [item for item in output if isinstance(item, dict)] if isinstance(output, list) else []
    print(f"site: {args.site}")
    print(f"phase: {args.phase}")
    print(f"snapshot_file: {snapshot_path}")
    print(f"snapshot_chars: {min(len(snapshot_text), args.max_snapshot_chars)}")
    print(f"tool_set: {args.tool_set}")
    print(f"output_item_types: {[str(item.get('type') or '') for item in output_items]}")
    print(f"stream_event_types: {response.get('stream_event_types') if isinstance(response, dict) else []}")
    output_text = response.get("output_text") if isinstance(response, dict) else ""
    if isinstance(output_text, str) and output_text.strip():
        print(f"output_text: {output_text.strip()[:1000]}")
    function_calls = [item for item in output_items if str(item.get("type") or "") == "function_call"]
    for item in function_calls:
        print("--- function_call ---")
        print(f"name: {item.get('name')}")
        print(f"arguments: {item.get('arguments')}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe browser runtime Responses behavior.")
    parser.add_argument("--site", default="microsoft", help="Site key, for example microsoft or nvidia.")
    parser.add_argument("--phase", default="session_preparation", help="Phase slug to probe.")
    parser.add_argument("--entry-url", default="", help="Optional entry URL shown in the prompt.")
    parser.add_argument(
        "--tool-set",
        choices=["phase_only", "phase_and_dummy_browser"],
        default="phase_and_dummy_browser",
        help="Whether to probe with only phase_result or with a few dummy browser tools too.",
    )
    parser.add_argument("--snapshot-file", default="", help="Optional explicit snapshot/session file path.")
    parser.add_argument("--max-snapshot-chars", type=int, default=12000, help="Max chars loaded from the snapshot file.")
    return parser.parse_args()


def main() -> int:
    return anyio.run(_run_probe, _parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
