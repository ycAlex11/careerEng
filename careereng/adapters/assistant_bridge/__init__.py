"""Assistant bridge for external AI assistants."""

from .intake import ingest_assistant_message
from .thread_state import AssistantThreadStateStore

__all__ = ["AssistantThreadStateStore", "ingest_assistant_message"]
