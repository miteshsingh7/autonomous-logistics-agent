"""Trajectory scoring.

We score the *trajectory* (the full step sequence), not just the final answer.
Four dimensions, each normalised to [0, 1]:

  1. tool_accuracy   - were tool calls valid, complete, and correctly ordered?
  2. reasoning       - LLM-as-judge rubric on the quality of reasoning
  3. error_recovery  - did the agent self-heal from an injected fault?
  4. task_success    - did it reach the expected outcome (reroute / escalate)?

Rule-based dimensions are deterministic; reasoning uses a judge model (with a
deterministic heuristic fallback in mock mode).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Optional

from agent.models import ModelClient
from agent.state import Step
from agent.tools import TOOLS


@dataclass
class Scorecard:
    scenario_id: str
    model: str
    tool_accuracy: float
    reasoning: float
    error_recovery: Optional[float]  # None when no fault was injected
    task_success: float
    steps: int
    notes: str = ""

    def overall(self) -> float:
        """Weighted composite. Recovery is dropped when not applicable."""
        parts = [
            (self.tool_accuracy, 0.30),
            (self.reasoning, 0.30),
            (self.task_success, 0.25),
        ]
        if self.error_recovery is not None:
            parts.append((self.error_recovery, 0.15))
        total_w = sum(w for _, w in parts)
        return round(sum(v * w for v, w in parts) / total_w, 3)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["overall"] = self.overall()
        return d


# --- rule-based dimensions --------------------------------------------------

def score_tool_accuracy(trajectory: list[Step], required_tools: list[str]) -> tuple[float, str]:
    calls = [s for s in trajectory if s.kind == "tool_call"]
    if not calls:
        return 0.0, "no tool calls made"

    valid = 0
    for s in calls:
        spec = TOOLS.get(s.tool or "")
        if not spec:
            continue
        if all(a in s.args for a in spec["required_args"]):
            valid += 1
    validity = valid / len(calls)

    called_names = [s.tool for s in calls]
    coverage = sum(1 for t in required_tools if t in called_names) / len(required_tools)

    # Ordering: telemetry must precede reroute; reroute must be last tool call.
    ordering = 1.0
    if "get_telemetry" in called_names and "execute_reroute" in called_names:
        if called_names.index("get_telemetry") > called_names.index("execute_reroute"):
            ordering = 0.0
    if "execute_reroute" in called_names and called_names[-1] != "execute_reroute":
        ordering = min(ordering, 0.5)

    score = round(0.5 * validity + 0.3 * coverage + 0.2 * ordering, 3)
    note = f"validity={validity:.2f} coverage={coverage:.2f} ordering={ordering:.2f}"
    return score, note


def score_error_recovery(trajectory: list[Step], has_fault: bool,
                          reached_success: bool) -> Optional[float]:
    if not has_fault:
        return None
    errored = any(s.kind == "error" for s in trajectory)
    if not errored:
        return 0.5  # fault configured but never triggered - inconclusive
    # Recovery = an error occurred AND the run still reached a good outcome
    # with a successful observation after the error.
    recovered_obs = False
    seen_error = False
    for s in trajectory:
        if s.kind == "error":
            seen_error = True
        elif s.kind == "observation" and s.ok and seen_error:
            recovered_obs = True
    return round(1.0 if (recovered_obs and reached_success) else 0.0, 3)


def score_task_success(decision: dict[str, Any], expect: dict[str, Any]) -> tuple[float, str]:
    action = (decision or {}).get("action", "none")
    if expect.get("should_reroute"):
        if action == "rerouted":
            return 1.0, "rerouted as expected"
        if action == "escalate":
            return 0.3, "escalated when a reroute was expected"
        return 0.0, f"no reroute (action={action})"
    # If no reroute was expected, escalation/hold is correct.
    return (1.0, "held/escalated as expected") if action != "rerouted" \
        else (0.0, "rerouted when it should not have")


# --- reasoning: LLM-as-judge ------------------------------------------------

_JUDGE_PROMPT = """You are grading the reasoning quality of a logistics agent's \
trajectory. Consider whether each decision was justified, whether it used \
evidence from tool results, and whether it avoided unsupported leaps.

Return ONLY JSON: {{"score": <0-5 integer>, "reason": "<one sentence>"}}.

Trajectory:
{traj}
"""


def score_reasoning(trajectory: list[Step], judge: ModelClient) -> tuple[float, str]:
    thoughts = [f"- {s.content}" for s in trajectory if s.kind == "thought"]
    if not thoughts:
        return 0.0, "no reasoning recorded"

    if judge.mock:
        return _mock_reasoning(trajectory)

    prompt = _JUDGE_PROMPT.format(traj="\n".join(thoughts))
    raw = judge.complete([{"role": "user", "content": prompt}])
    try:
        data = json.loads(raw)
        score = max(0, min(5, int(data.get("score", 0))))
        return round(score / 5.0, 3), str(data.get("reason", ""))[:120]
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0.4, "judge output unparseable; default applied"


def _mock_reasoning(trajectory: list[Step]) -> tuple[float, str]:
    """Deterministic heuristic used in offline mode.

    Rewards: reasoning that references evidence, confirms a quote before acting,
    and recovers from errors. This lets the offline harness produce a plausible,
    non-trivial comparison without a judge model.
    """
    thoughts = " ".join(s.content.lower() for s in trajectory if s.kind == "thought")
    calls = [s.tool for s in trajectory if s.kind == "tool_call"]
    score = 3.0
    if "estimate_cost_eta" in calls:
        score += 1.0  # validated a firm quote before dispatching
    if any(w in thoughts for w in ("retry", "failed", "corrected")):
        score += 0.5  # acknowledged and handled failure
    if any(w in thoughts for w in ("eta", "reliability", "$", "cost")):
        score += 0.5  # grounded in tool evidence
    score = min(5.0, score)
    return round(score / 5.0, 3), "heuristic (mock judge)"
