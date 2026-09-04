"""Assistant bridge message intake orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .processors.local import LocalProcessorAdapter
from careereng.orchestration.agent_protocol.assistant_bridge import (
    DATA_CATEGORY_APPLICATION_FEEDBACK,
    DATA_CATEGORY_CAREER_INTENT_STRATEGY,
    DATA_CATEGORY_CORRECTION,
    DATA_CATEGORY_INTERVIEW_RECORD,
    DATA_CATEGORY_PROFILE_RESUME_SIGNAL,
    SCOPED_DATA_CATEGORIES,
    TRIGGER_EXPLICIT,
    TRIGGER_IMPLICIT_SUGGESTED,
    TRIGGER_NONE,
    TRIGGER_SCOPE_FOLLOWUP,
)
from .store import AssistantBridgeStore
from .thread_state import AssistantThreadStateStore
from careereng.platform.project_state import AgentEventStore
from careereng.utils import make_id


def _normalize_assistant_trigger(message: str) -> tuple[bool, str]:
    raw = str(message or "").strip()
    lowered = raw.lower()
    for prefix in ("@career", "＠career"):
        if lowered.startswith(prefix):
            return True, raw[len(prefix) :].strip()
    return False, raw


def _is_scope_end_message(normalized_message: str) -> bool:
    text = str(normalized_message or "").strip().lower()
    return text in {"end", "结束", "结束上下文", "end scope", "career end", "stop scope"}


def _thread_id_or_default(thread_id: str) -> str:
    return str(thread_id or "default").strip() or "default"


def _session_id_or_default(*, session_id: str, client: str, thread_id: str) -> str:
    text = str(session_id or "").strip()
    if text:
        return text
    return f"assistant:{client or 'unknown'}:{thread_id or 'default'}"


def _signal_payload(
    *,
    intake_event_id: str,
    source_text: str,
    decision: dict[str, Any],
) -> dict[str, Any]:
    return {
        "intake_event_id": intake_event_id,
        "source_text": source_text,
        "semantic_labels": decision.get("semantic_labels") or [],
        "detected_entities": decision.get("detected_entities") or {},
        "confidence": decision.get("confidence", 0.0),
        "candidate_patch": {},
        "status": "raw",
    }


def ingest_assistant_message(
    *,
    workspace: Path | str,
    message: str,
    client: str = "codex",
    thread_id: str = "default",
    session_id: str = "",
    processor_backend: str = "local",
) -> dict[str, Any]:
    workspace_path = Path(workspace)
    client_text = str(client or "unknown").strip() or "unknown"
    thread_text = _thread_id_or_default(thread_id)
    session_text = _session_id_or_default(session_id=session_id, client=client_text, thread_id=thread_text)
    explicit_trigger, normalized_message = _normalize_assistant_trigger(message)

    store = AssistantBridgeStore(workspace_path)
    thread_store = AssistantThreadStateStore(workspace_path)
    existing_state = thread_store.get(client=client_text, thread_id=thread_text)
    inherited_scope = bool(existing_state.get("active")) and not explicit_trigger
    active_category = str(existing_state.get("active_category") or "")

    processor = LocalProcessorAdapter()
    if str(processor_backend or "local").strip().lower() != "local":
        processor_backend = "local"
    decision = processor.classify(
        message=normalized_message,
        context={
            "client": client_text,
            "thread_id": thread_text,
            "session_id": session_text,
            "explicit_trigger": explicit_trigger,
            "inherited_scope": inherited_scope,
            "active_category": active_category,
            "thread_state": existing_state,
        },
    )
    decision_dict = decision.to_dict()
    data_category = str(decision_dict.get("data_category") or "")

    if explicit_trigger:
        trigger_mode = TRIGGER_EXPLICIT
    elif inherited_scope:
        trigger_mode = TRIGGER_SCOPE_FOLLOWUP
    elif bool(decision_dict.get("should_save")) and float(decision_dict.get("confidence") or 0.0) >= 0.7:
        trigger_mode = TRIGGER_IMPLICIT_SUGGESTED
    else:
        trigger_mode = TRIGGER_NONE

    should_save = bool(decision_dict.get("should_save")) and trigger_mode != TRIGGER_NONE
    should_execute = False
    event_id = make_id("aintake") if should_save else ""
    scope_id = str(existing_state.get("scope_id") or "")
    thread_scope = "active" if inherited_scope else "none"

    if explicit_trigger and _is_scope_end_message(normalized_message):
        closed_state = thread_store.close_scope(client=client_text, thread_id=thread_text)
        scope_id = str(closed_state.get("scope_id") or scope_id)
        thread_scope = "closed"
        should_save = True
        if not event_id:
            event_id = make_id("aintake")
        decision_dict["suggested_action"] = "assistant_scope_end"
        decision_dict["suggested_command"] = ""
        decision_dict["should_save"] = True
        data_category = str(decision_dict.get("data_category") or data_category)
    elif explicit_trigger and data_category in SCOPED_DATA_CATEGORIES:
        opened_state = thread_store.open_scope(
            client=client_text,
            thread_id=thread_text,
            category=data_category,
            topic=normalized_message[:180],
            opened_by_event_id=event_id,
        )
        scope_id = str(opened_state.get("scope_id") or "")
        thread_scope = "opened"
    elif inherited_scope:
        touched = thread_store.touch(client=client_text, thread_id=thread_text)
        scope_id = str(touched.get("scope_id") or scope_id)
        thread_scope = "active"

    output: dict[str, Any] = {
        "event_id": event_id,
        "client": client_text,
        "thread_id": thread_text,
        "session_id": session_text,
        "raw_message": str(message or ""),
        "normalized_message": normalized_message,
        "explicit_trigger": explicit_trigger,
        "inherited_scope": inherited_scope,
        "trigger_mode": trigger_mode,
        "scope_id": scope_id,
        "thread_scope": thread_scope,
        "data_category": data_category,
        "route": decision_dict.get("route", ""),
        "confidence": decision_dict.get("confidence", 0.0),
        "semantic_labels": decision_dict.get("semantic_labels") or [],
        "detected_entities": decision_dict.get("detected_entities") or {},
        "suggested_action": decision_dict.get("suggested_action", ""),
        "suggested_command": decision_dict.get("suggested_command", ""),
        "should_save": should_save,
        "should_execute": should_execute,
        "reason": decision_dict.get("reason", ""),
        "processor_backend": decision_dict.get("processor_backend", "local"),
        "processor_version": decision_dict.get("processor_version", "v1"),
        "processor_trace": decision_dict.get("processor_trace") or {},
        "memory_unit_ids": [],
        "routing_example_ids": [],
        "embedding_ref": "",
        "vector_ref": "",
    }

    if client_text == "codex" and thread_text != "default":
        event_store = AgentEventStore(workspace_path)
        try:
            output["main_agent_registration"] = event_store.register_main_agent(
                thread_id=thread_text,
                consumer_id="codex_desktop",
            )
            inbox = event_store.list_events(consumer_id="codex_desktop", limit=20)
            output["main_agent_inbox"] = {
                "cursor": inbox["cursor"],
                "next_cursor": inbox["next_cursor"],
                "events": inbox["events"],
                "has_attention_required": inbox["has_attention_required"],
            }
        except ValueError as exc:
            output["main_agent_registration"] = {
                "status": "conflict",
                "error": str(exc),
            }

    if not should_save:
        return output

    intake_event = store.append_intake_event(output)
    event_id = str(intake_event.get("event_id") or event_id)
    output["event_id"] = event_id

    action_ids: list[str] = []
    suggested_action = str(output.get("suggested_action") or "")
    if suggested_action:
        action_event = store.append_action_event(
            {
                "intake_event_id": event_id,
                "action_type": suggested_action,
                "command": str(output.get("suggested_command") or ""),
                "client": client_text,
                "thread_id": thread_text,
                "session_id": session_text,
                "status": "suggested",
                "exit_code": None,
                "stdout_summary": "",
                "stderr_summary": "",
                "result_ref": "",
            }
        )
        action_ids.append(str(action_event.get("action_event_id") or ""))

    memory_unit_ids: list[str] = []
    base_signal = _signal_payload(intake_event_id=event_id, source_text=normalized_message, decision=output)
    if data_category == DATA_CATEGORY_PROFILE_RESUME_SIGNAL:
        signal = store.append_profile_signal({**base_signal, "signal_type": "profile_resume", "subject": "", "evidence": normalized_message})
        memory_unit_ids.append(str(signal.get("signal_id") or ""))
    elif data_category == DATA_CATEGORY_CAREER_INTENT_STRATEGY:
        signal = store.append_intent_signal(
            {
                **base_signal,
                "target_role": "",
                "target_company": "",
                "target_domain": "",
                "constraints": [],
                "plan_summary": "",
            }
        )
        memory_unit_ids.append(str(signal.get("signal_id") or ""))
    elif data_category == DATA_CATEGORY_APPLICATION_FEEDBACK:
        signal = store.append_application_feedback_signal(
            {
                **base_signal,
                "site_key": str(output.get("detected_entities", {}).get("site_key") or ""),
                "company": str(output.get("detected_entities", {}).get("company") or ""),
                "job_id": "",
                "job_title": "",
                "feedback_type": "",
                "observed_status": "",
                "strategy_implication": "",
            }
        )
        memory_unit_ids.append(str(signal.get("signal_id") or ""))
    elif data_category == DATA_CATEGORY_INTERVIEW_RECORD:
        signal = store.append_interview_event(
            {
                **base_signal,
                "company": str(output.get("detected_entities", {}).get("company") or ""),
                "site_key": str(output.get("detected_entities", {}).get("site_key") or ""),
                "job_id": "",
                "job_title": "",
                "event_type": "assistant_message",
                "content": normalized_message,
                "source": client_text,
            }
        )
        memory_unit_ids.append(str(signal.get("interview_event_id") or ""))
    elif data_category == DATA_CATEGORY_CORRECTION:
        correction = store.append_correction_event(
            {
                "intake_event_id": event_id,
                "wrong_route": "",
                "wrong_action": "",
                "correct_route": "",
                "correct_action": "",
                "user_correction": normalized_message,
                "confidence": output.get("confidence", 0.0),
            }
        )
        memory_unit_ids.append(str(correction.get("correction_id") or ""))

    routing_example_ids: list[str] = []
    if explicit_trigger or data_category == DATA_CATEGORY_CORRECTION:
        example = store.append_routing_example(
            {
                "text": normalized_message,
                "expected_category": data_category,
                "expected_action": suggested_action,
                "label_source": "correction" if data_category == DATA_CATEGORY_CORRECTION else "explicit_trigger",
                "is_positive": True,
                "confidence": output.get("confidence", 0.0),
                "created_from_event_id": event_id,
                "semantic_labels": output.get("semantic_labels") or [],
                "detected_entities": output.get("detected_entities") or {},
            }
        )
        routing_example_ids.append(str(example.get("routing_example_id") or ""))

    output["action_event_ids"] = [item for item in action_ids if item]
    output["memory_unit_ids"] = [item for item in memory_unit_ids if item]
    output["routing_example_ids"] = [item for item in routing_example_ids if item]
    return output
