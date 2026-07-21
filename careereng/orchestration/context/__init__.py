"""Context contracts and resources assembled for one orchestration unit."""

from careereng.orchestration.context.bundles import BrowserContextSession
from careereng.orchestration.context.phase import PhaseContext, build_phase_context
from careereng.orchestration.context.phase_memory import BrowserPhaseMemory
from careereng.orchestration.context.prompts import PhasePrompt, build_phase_prompts, load_text
from careereng.orchestration.context.registry import BrowserContextRegistry
from careereng.orchestration.context.resources import (
    CONTEXT_RESOURCE_IDS,
    ContextResourceResolver,
    build_apply_initial_facts,
    render_apply_facts,
)
from careereng.orchestration.context.workflow_memory import (
    WorkflowMemoryStore,
    extract_failure_snapshot_from_trace,
    record_interrupted_batches,
)

__all__ = [
    "BrowserContextRegistry",
    "BrowserContextSession",
    "ContextResourceResolver",
    "CONTEXT_RESOURCE_IDS",
    "build_apply_initial_facts",
    "render_apply_facts",
    "BrowserPhaseMemory",
    "PhaseContext",
    "PhasePrompt",
    "WorkflowMemoryStore",
    "build_phase_context",
    "build_phase_prompts",
    "extract_failure_snapshot_from_trace",
    "record_interrupted_batches",
    "load_text",
]
