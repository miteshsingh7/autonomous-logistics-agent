"""Shared graph state and trajectory primitives.

The state object flows through every LangGraph node. Because the whole run is
captured as an ordered list of `Step` records, the same structure that drives
the agent also *is* the artifact we evaluate in Part 1 (trajectory-based eval).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, TypedDict


StepKind = Literal["thought", "tool_call", "observation", "error", "final"]


@dataclass
class Step:
    """One entry in the agent trajectory.

    A trajectory is the ordered sequence of everything the agent did: what it
    was thinking, which tool it called with which arguments, what it observed,
    any errors, and the final decision. Trajectory-based evaluation scores this
    whole sequence rather than only the final answer.
    """

    kind: StepKind
    # For tool_call: the tool name + arguments. For thought/final: free text.
    tool: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    content: str = ""
    # Populated for observation/error steps produced by tool execution.
    ok: Optional[bool] = None
    # Which specialist agent produced this step (assessor/planner/executor).
    agent: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "agent": self.agent,
            "tool": self.tool,
            "args": self.args,
            "content": self.content,
            "ok": self.ok,
        }


class AgentState(TypedDict, total=False):
    """LangGraph state for the autonomous rerouting agent."""

    # Inputs
    scenario_id: str
    alert: dict[str, Any]          # the incoming telemetry alert
    model: str                     # gateway model string in use

    # Working memory (shared "blackboard" the specialist agents read/write)
    trajectory: list[Step]         # full ordered history (the eval artifact)
    tool_results: dict[str, Any]   # latest structured result per tool
    fault_injected: bool           # whether a tool failure was injected
    fault_cleared: bool            # whether an agent recovered from it

    # Multi-agent hand-off artifacts
    assessment: dict[str, Any]     # assessor -> {"reroute_needed": bool, "reason": ...}
    plan: dict[str, Any]           # planner  -> {"selected": {...}|None, "reason": ...}
    active_agent: str              # label of the agent that last held control

    # Control
    step_count: int                # total model calls across all agents
    max_steps: int                 # per-agent internal step budget

    # Output
    decision: dict[str, Any]       # final reroute decision / escalation
    done: bool
