"""Application search, history, planning, and reporting capabilities."""

from typing import TYPE_CHECKING

from .history_repair import (
    HISTORY_REPAIR_PLAN_RELATIVE_PATH,
    inspect_history_repairs,
    save_history_repair_plan,
)
from .reports import generate_job_batch_report
from .summary import (
    APPLICATION_SUMMARY_RELATIVE_PATH,
    build_application_summary,
    save_application_summary,
)

__all__ = [
    "APPLICATION_SUMMARY_RELATIVE_PATH",
    "HISTORY_REPAIR_PLAN_RELATIVE_PATH",
    "ApplicationPlanningService",
    "build_application_summary",
    "generate_job_batch_report",
    "inspect_history_repairs",
    "save_application_summary",
    "save_history_repair_plan",
]

if TYPE_CHECKING:
    from .planning import ApplicationPlanningService


def __getattr__(name: str):
    if name == "ApplicationPlanningService":
        from .planning import ApplicationPlanningService

        return ApplicationPlanningService
    raise AttributeError(name)
