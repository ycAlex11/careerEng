"""Generic item-loop evolution orchestration.

This module keeps loop control generic: Python decides orchestration state,
while LLM/Skills/proposals provide the strategy content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from careereng.evolution.loop_control import (
    LOOP_ACTION_PAUSE_BATCH,
    LOOP_ACTION_PAUSE_SITE,
    LOOP_ACTION_REQUEST_USER_INPUT,
    LOOP_ACTION_TRIGGER_REFINEMENT,
    loop_control_is_human_only_gap,
)


ITEM_LOOP_CONTINUE = "continue"
ITEM_LOOP_HOLD_FOR_RUN_LOCAL_CHANGE = "hold_for_run_local_change"
ITEM_LOOP_RESUME_WITH_RUN_LOCAL_CHANGE = "resume_with_run_local_change"
ITEM_LOOP_PAUSE_FOR_USER_INPUT = "pause_for_user_input"
ITEM_LOOP_PAUSE_THRESHOLD = "pause_threshold"
ITEM_LOOP_PAUSE_EXPLICIT = "pause_explicit"


@dataclass(frozen=True)
class ItemLoopTransition:
    """Decision for one item-loop trigger.

    `hold_next_item` means the current item produced enough evidence that the
    next item must not run under the same stale strategy. It is not a browser
    observation blocker.
    """

    action: str
    hold_next_item: bool = False
    pause_loop: bool = False
    requires_materialized_change: bool = False
    should_create_solution_request: bool = False
    reason_tag: str = "item_loop_continue"
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "hold_next_item": self.hold_next_item,
            "pause_loop": self.pause_loop,
            "requires_materialized_change": self.requires_materialized_change,
            "should_create_solution_request": self.should_create_solution_request,
            "reason_tag": self.reason_tag,
            "message": self.message,
        }


def plan_item_loop_transition(
    control: dict[str, Any] | None,
    *,
    attempts: int,
    max_refinement_attempts: int,
    max_user_input_attempts: int,
    has_materialized_change: bool,
    artifacts: dict[str, Any] | None = None,
) -> ItemLoopTransition:
    """Plan item-loop orchestration from LLM-provided loop control evidence.

    This function intentionally does not infer business meaning. It only maps a
    normalized loop-control action and proposal state to a reusable loop
    transition that apply-list and future item loops can share.
    """

    payload = control if isinstance(control, dict) else {}
    action = str(payload.get("action") or "").strip()
    artifact_payload = artifacts if isinstance(artifacts, dict) else {}
    attempt_count = max(0, int(attempts or 0))
    refinement_limit = max(1, int(max_refinement_attempts or 1))
    user_input_limit = max(1, int(max_user_input_attempts or 1))

    if not action:
        return ItemLoopTransition(
            action=ITEM_LOOP_CONTINUE,
            reason_tag="item_loop_continue",
            message="No loop-control action was provided.",
        )

    if action in {LOOP_ACTION_PAUSE_SITE, LOOP_ACTION_PAUSE_BATCH}:
        return ItemLoopTransition(
            action=ITEM_LOOP_PAUSE_EXPLICIT,
            hold_next_item=True,
            pause_loop=True,
            reason_tag="item_loop_pause_explicit",
            message=f"Loop explicitly requested `{action}`.",
        )

    if loop_control_is_human_only_gap(payload):
        return ItemLoopTransition(
            action=ITEM_LOOP_PAUSE_FOR_USER_INPUT,
            hold_next_item=True,
            pause_loop=True,
            reason_tag="item_loop_waiting_user_input",
            message="Loop-control evidence requires human-only input.",
        )

    if action != LOOP_ACTION_TRIGGER_REFINEMENT and bool(artifact_payload.get("escalated")):
        return ItemLoopTransition(
            action=ITEM_LOOP_PAUSE_THRESHOLD,
            hold_next_item=True,
            pause_loop=True,
            reason_tag="item_loop_escalated",
            message="Loop-control evidence has been escalated by the evidence store.",
        )

    if action == LOOP_ACTION_REQUEST_USER_INPUT:
        pause = attempt_count >= user_input_limit
        return ItemLoopTransition(
            action=ITEM_LOOP_PAUSE_FOR_USER_INPUT if pause else ITEM_LOOP_CONTINUE,
            hold_next_item=pause,
            pause_loop=pause,
            reason_tag="item_loop_waiting_user_input" if pause else "item_loop_user_input_recorded",
            message=(
                "User input threshold reached for this loop pattern."
                if pause
                else "User-input evidence recorded; threshold has not been reached."
            ),
        )

    if action == LOOP_ACTION_TRIGGER_REFINEMENT:
        if attempt_count >= refinement_limit:
            return ItemLoopTransition(
                action=ITEM_LOOP_PAUSE_THRESHOLD,
                hold_next_item=True,
                pause_loop=True,
                requires_materialized_change=False,
                should_create_solution_request=False,
                reason_tag="item_loop_refinement_threshold",
                message="Refinement threshold reached for this loop pattern.",
            )
        if has_materialized_change:
            return ItemLoopTransition(
                action=ITEM_LOOP_RESUME_WITH_RUN_LOCAL_CHANGE,
                reason_tag="item_loop_resume_with_run_local_change",
                message="A materialized run-local change exists; the next item may validate it.",
            )
        return ItemLoopTransition(
            action=ITEM_LOOP_HOLD_FOR_RUN_LOCAL_CHANGE,
            hold_next_item=True,
            pause_loop=True,
            requires_materialized_change=True,
            should_create_solution_request=True,
            reason_tag="item_loop_waiting_run_local_change",
            message="Hold the next item until a concrete run-local change is materialized.",
        )

    return ItemLoopTransition(
        action=ITEM_LOOP_CONTINUE,
        reason_tag="item_loop_recorded",
        message=f"Loop-control action `{action}` was recorded without pausing the item loop.",
    )
