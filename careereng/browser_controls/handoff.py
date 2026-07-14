"""Compatibility imports for legacy browser handoff code.

New code should use :mod:`careereng.agent_bridge.work_orders`.
"""

from __future__ import annotations

from careereng.agent_bridge.work_orders import AgentBridgeWorkOrder, create_browser_agent_work_order

CodexBrowserHandoff = AgentBridgeWorkOrder
create_codex_browser_handoff = create_browser_agent_work_order

__all__ = [
    "AgentBridgeWorkOrder",
    "CodexBrowserHandoff",
    "create_browser_agent_work_order",
    "create_codex_browser_handoff",
]
