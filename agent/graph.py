"""The autonomous rerouting workflow as a MULTI-AGENT LangGraph state machine.

Rather than one monolithic ReAct loop, the workload is split across three
specialist agents coordinated by a supervisor (the orchestrator). Each agent
owns a narrow slice of tools and a single responsibility, and they communicate
through a shared "blackboard" state object:

    ┌────────────────────────── orchestrator ──────────────────────────┐
    │  routes on shared state: assessment? plan? decision?              │
    └───────┬───────────────────┬───────────────────────┬──────────────┘
            ▼                   ▼                       ▼
      ┌───────────┐       ┌───────────┐           ┌───────────┐
      │ assessor  │       │  planner  │           │ executor  │
      │ telemetry │──────▶│ alts+quote│──────────▶│ dispatch  │
      └───────────┘       └───────────┘           └───────────┘
        (get_telemetry)   (find_alternatives,       (execute_reroute)
                           estimate_cost_eta)

Why multi-agent:
- Separation of concerns keeps each agent's prompt + tool surface small, which
  measurably improves tool-calling reliability (fewer tools to choose from).
- The supervisor makes control flow explicit and auditable - the hand-off
  artifacts (assessment, plan, decision) are inspectable between every agent.
- Each agent runs its own bounded ReAct loop and self-heals against failures in
  ITS OWN tools, which is exactly what a proactive, self-healing logistics
  system needs.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from langgraph.graph import END, StateGraph

from .models import ModelClient
from .state import AgentState, Step
from .tools import TOOLS, ToolError, tool_specs_for_prompt

# --- per-agent configuration ------------------------------------------------

AGENT_TOOLS: dict[str, list[str]] = {
    "assessor": ["get_telemetry"],
    "planner": ["find_alternatives", "estimate_cost_eta"],
    "executor": ["execute_reroute"],
}

_BASE_RULES = """You are ONE specialist agent inside a multi-agent logistics \
rerouting system. Respond with a SINGLE JSON object and nothing else, each turn.

Call a tool:
  {{"thought": "<reasoning>", "tool": "<name>", "args": {{...}}}}
or hand back control to the supervisor when your job is done:
  {{"thought": "<reasoning>", "handoff": {{...}}}}

If a tool call fails, inspect the error and RETRY THE SAME TOOL with corrected
arguments before giving up. Only the tools listed below are available to you.

Your tools:
{tools}
"""

_ROLE_BRIEF: dict[str, str] = {
    "assessor": (
        "ROLE: assessor. Pull live telemetry for the shipment, then decide "
        "whether a reroute is warranted. Hand off with "
        '{{"reroute_needed": <bool>, "reason": "<why>"}}.'
    ),
    "planner": (
        "ROLE: planner. Enumerate alternative carriers/routes, then confirm a "
        "firm cost/ETA quote for the best option before recommending it. Prefer "
        "the best reliability-adjusted ETA at reasonable cost. Hand off with "
        '{{"selected": {{"carrier","route","eta_hours","cost_usd"}} or null, '
        '"reason": "<why>"}}.'
    ),
    "executor": (
        "ROLE: executor. Commit the reroute the planner selected via the "
        "dispatch system, then hand off with "
        '{{"action": "rerouted", "carrier","route","eta_hours","cost_usd"}} '
        '(or {{"action": "escalate", "reason": ...}} if dispatch is impossible).'
    ),
}


def build_agent(model: str, mock: bool | None = None):
    """Compile and return the runnable multi-agent workflow for a given model."""
    client = ModelClient(model, mock=mock)
    graph = StateGraph(AgentState)

    graph.add_node("orchestrator", _orchestrator)
    graph.add_node("assessor", _make_agent_node("assessor", client))
    graph.add_node("planner", _make_agent_node("planner", client))
    graph.add_node("executor", _make_agent_node("executor", client))

    graph.set_entry_point("orchestrator")
    graph.add_conditional_edges(
        "orchestrator",
        _route,
        {"assessor": "assessor", "planner": "planner",
         "executor": "executor", "end": END},
    )
    # Every specialist returns control to the supervisor.
    graph.add_edge("assessor", "orchestrator")
    graph.add_edge("planner", "orchestrator")
    graph.add_edge("executor", "orchestrator")
    return graph.compile()


# --- supervisor -------------------------------------------------------------

def _orchestrator(state: AgentState) -> dict[str, Any]:
    """Pure routing bookkeeping; seeds the trajectory on first entry."""
    if "trajectory" not in state:
        alert = state["alert"]
        seed = Step(
            kind="observation", agent="orchestrator", tool="alert", ok=True,
            content=(f"ALERT {alert['shipment_id']}: {alert['description']} "
                     f"(severity={alert.get('severity', 'unknown')})"),
        )
        return {
            "trajectory": [seed],
            "tool_results": {},
            "step_count": 0,
            "max_steps": state.get("max_steps", 6),
            "fault_injected": False,
            "fault_cleared": False,
        }
    return {}


def _route(state: AgentState) -> str:
    """Decide which specialist runs next based purely on shared state."""
    if state.get("decision"):
        return "end"
    if "assessment" not in state:
        return "assessor"
    if "plan" not in state:
        return "planner"
    return "executor"


# --- specialist agent node factory ------------------------------------------

def _make_agent_node(role: str, client: ModelClient) -> Callable[[AgentState], dict[str, Any]]:
    allowed = AGENT_TOOLS[role]
    system = (
        _BASE_RULES.format(tools=tool_specs_for_prompt(allowed))
        + "\n" + _ROLE_BRIEF[role]
    )

    def _node(state: AgentState) -> dict[str, Any]:
        alert = state["alert"]
        trajectory = list(state["trajectory"])
        tool_results = dict(state["tool_results"])
        fault_injected = state.get("fault_injected", False)
        fault_cleared = state.get("fault_cleared", False)
        step_count = state.get("step_count", 0)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": _agent_briefing(role, state)},
        ]

        update: dict[str, Any] = {"active_agent": role}
        budget = state.get("max_steps", 6)

        for _ in range(budget):
            raw = client.complete(messages)
            step_count += 1
            thought, action = _parse_action(raw)
            trajectory.append(Step(kind="thought", agent=role, content=thought))
            messages.append({"role": "assistant", "content": raw})

            if "handoff" in action:
                _apply_handoff(role, action["handoff"], update, trajectory)
                break

            # tool call
            name = action.get("tool", "")
            args = action.get("args", {})
            trajectory.append(Step(kind="tool_call", agent=role, tool=name, args=args))

            result, ok, fault_injected, fault_cleared = _execute_tool(
                name, args, allowed, alert, fault_injected, fault_cleared,
            )
            if ok:
                tool_results[name] = {k: v for k, v in result.items() if k != "ok"}
                trajectory.append(Step(kind="observation", agent=role, tool=name,
                                       content=json.dumps(result), ok=True))
            else:
                trajectory.append(Step(kind="error", agent=role, tool=name,
                                       content=result["error"], ok=False))
            messages.append({"role": "tool", "content": json.dumps(result)})
        else:
            # Budget exhausted without a hand-off: fail safe by escalating.
            update.setdefault("decision", {"action": "escalate",
                                           "reason": f"{role} exceeded step budget"})

        update.update({
            "trajectory": trajectory,
            "tool_results": tool_results,
            "fault_injected": fault_injected,
            "fault_cleared": fault_cleared,
            "step_count": step_count,
        })
        return update

    return _node


def _agent_briefing(role: str, state: AgentState) -> str:
    """The task-specific context handed to each specialist."""
    alert = state["alert"]
    header = (f"Shipment {alert['shipment_id']}. Alert: {alert['description']} "
              f"(severity={alert.get('severity', 'unknown')}).")
    if role == "assessor":
        return header + " Assess and decide whether to reroute."
    if role == "planner":
        reason = state.get("assessment", {}).get("reason", "")
        return (header + f" A reroute is warranted ({reason}). "
                "Find and select the best alternative.")
    if role == "executor":
        plan = state.get("plan", {}).get("selected", {})
        return (header + f" Execute the planner's selection: PLAN={json.dumps(plan)}.")
    return header


def _apply_handoff(role: str, handoff: dict[str, Any],
                   update: dict[str, Any], trajectory: list[Step]) -> None:
    """Translate an agent's hand-off into shared-state artifacts."""
    trajectory.append(Step(kind="final", agent=role, content=json.dumps(handoff)))
    if role == "assessor":
        reroute_needed = bool(handoff.get("reroute_needed", True))
        update["assessment"] = {"reroute_needed": reroute_needed,
                                "reason": handoff.get("reason", "")}
        if not reroute_needed:
            update["decision"] = {"action": "hold", "reason": handoff.get("reason", "")}
    elif role == "planner":
        selected = handoff.get("selected")
        update["plan"] = {"selected": selected, "reason": handoff.get("reason", "")}
        if not selected:
            update["decision"] = {"action": "escalate",
                                  "reason": handoff.get("reason", "no viable option")}
    elif role == "executor":
        update["decision"] = handoff


# --- tool execution (shared) ------------------------------------------------

def _execute_tool(name: str, args: dict[str, Any], allowed: list[str],
                  alert: dict[str, Any], fault_injected: bool,
                  fault_cleared: bool) -> tuple[dict[str, Any], bool, bool, bool]:
    """Run a tool, enforcing per-agent scope and one-time fault injection."""
    fault = alert.get("_fault")

    if name not in allowed:
        return ({"ok": False, "error": f"tool '{name}' not available to this agent"},
                False, fault_injected, fault_cleared)

    # Inject a one-time failure to exercise self-healing.
    if fault and not fault_injected and fault.get("tool") == name:
        return ({"ok": False, "error": fault.get("message", "transient backend error")},
                False, True, fault_cleared)

    if name not in TOOLS:
        return ({"ok": False, "error": f"unknown tool '{name}'"},
                False, fault_injected, fault_cleared)

    try:
        payload = TOOLS[name]["fn"](**args)
        if fault_injected:
            fault_cleared = True  # a good call landed after a prior failure
        return ({"ok": True, **payload}, True, fault_injected, fault_cleared)
    except (ToolError, TypeError) as exc:
        return ({"ok": False, "error": str(exc)}, False, fault_injected, fault_cleared)


# --- parsing ----------------------------------------------------------------

def _parse_action(raw: str) -> tuple[str, dict[str, Any]]:
    """Extract (thought, action) from a model completion, defensively."""
    data = _loads_lenient(raw)
    thought = str(data.get("thought", "")) if isinstance(data, dict) else ""
    if isinstance(data, dict) and "handoff" in data:
        return thought, {"handoff": data["handoff"]}
    if isinstance(data, dict) and "tool" in data:
        return thought, {"tool": data["tool"], "args": data.get("args", {})}
    # Unparseable output -> hand off with an escalation so the loop terminates.
    return thought or raw[:200], {"handoff": {"action": "escalate",
                                              "reason": "unparseable model output"}}


def _loads_lenient(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return {}
        return {}
