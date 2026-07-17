"""Assistant bridge for external AI assistants."""

from careereng.integrations.assistant_bridge.intake import ingest_assistant_message
from careereng.integrations.assistant_bridge.thread_state import AssistantThreadStateStore

__all__ = ["AssistantThreadStateStore", "ingest_assistant_message"]

