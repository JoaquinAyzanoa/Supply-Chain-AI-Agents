"""Service-to-service plumbing.

- ``events``: signed events from mail_sync, scheduler and Odoo to the director.
- ``protocol``, ``server``, ``client``, ``trace``: agents exposed over A2A
  (a2a-sdk 1.x on FastAPI) with bearer auth and Langfuse trace propagation.
- ``testing``: doubles for callers and an in-process client.
"""

from sc_core.a2a.client import A2AClient, AgentCaller
from sc_core.a2a.protocol import AgentReply, AgentSpec, Skill, TaskHandler
from sc_core.a2a.server import mount
from sc_core.a2a.trace import propagate_from_metadata, trace_metadata

__all__ = [
    "A2AClient",
    "AgentCaller",
    "AgentReply",
    "AgentSpec",
    "Skill",
    "TaskHandler",
    "mount",
    "propagate_from_metadata",
    "trace_metadata",
]
