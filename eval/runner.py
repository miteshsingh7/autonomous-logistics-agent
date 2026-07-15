"""Run scenarios across both models, score trajectories, emit comparison tables.

Outputs (written to results/):
  - results/raw/<model>__<scenario>.json   full trajectories
  - results/scorecards.json                 every scorecard
  - results/comparison.md                   human-readable head-to-head
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tabulate import tabulate

from agent import build_agent
from agent.models import ModelClient
from agent.state import Step
from eval.scoring import (
    Scorecard,
    score_error_recovery,
    score_reasoning,
    score_task_success,
    score_tool_accuracy,
)
from scenarios import get_scenarios

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
RAW_DIR = RESULTS_DIR / "raw"


def run_all(models: dict[str, str], mock: bool | None = None) -> list[Scorecard]:
    """models maps a label ('closed'/'open') -> gateway model string."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    judge = ModelClient(models.get("closed", next(iter(models.values()))), mock=mock)

    scorecards: list[Scorecard] = []
    for label, model in models.items():
        agent = build_agent(model, mock=mock)
        for scenario in get_scenarios():
            card = _run_one(agent, judge, label, model, scenario)
            scorecards.append(card)

    _write_outputs(models, scorecards)
    return scorecards


def _run_one(agent, judge: ModelClient, label: str, model: str,
             scenario: dict[str, Any]) -> Scorecard:
    init = {
        "scenario_id": scenario["id"],
        "alert": scenario["alert"],
        "model": model,
        "max_steps": 8,
    }
    final_state = agent.invoke(init, config={"recursion_limit": 50})
    trajectory: list[Step] = final_state["trajectory"]
    decision = final_state.get("decision", {})
    expect = scenario["expect"]

    ta, ta_note = score_tool_accuracy(trajectory, expect["required_tools"])
    ts, ts_note = score_task_success(decision, expect)
    reached = ts >= 1.0
    rec = score_error_recovery(trajectory, expect["has_fault"], reached)
    rq, rq_note = score_reasoning(trajectory, judge)

    card = Scorecard(
        scenario_id=scenario["id"],
        model=f"{label}:{model}",
        tool_accuracy=ta,
        reasoning=rq,
        error_recovery=rec,
        task_success=ts,
        steps=sum(1 for s in trajectory if s.kind == "tool_call"),
        notes=f"{ta_note}; {ts_note}; {rq_note}",
    )

    # Persist the full trajectory for auditability.
    safe_model = model.replace("/", "_")
    raw_path = RAW_DIR / f"{safe_model}__{scenario['id']}.json"
    raw_path.write_text(json.dumps({
        "scenario": scenario["id"],
        "model": model,
        "decision": decision,
        "trajectory": [s.to_dict() for s in trajectory],
        "scorecard": card.to_dict(),
    }, indent=2))
    return card


def _write_outputs(models: dict[str, str], cards: list[Scorecard]) -> None:
    (RESULTS_DIR / "scorecards.json").write_text(
        json.dumps([c.to_dict() for c in cards], indent=2)
    )

    # Per-scenario table.
    headers = ["Scenario", "Model", "Tool", "Reason", "Recover", "Success", "Overall"]
    rows = []
    for c in cards:
        rows.append([
            c.scenario_id, c.model.split(":", 1)[0],
            f"{c.tool_accuracy:.2f}", f"{c.reasoning:.2f}",
            "-" if c.error_recovery is None else f"{c.error_recovery:.2f}",
            f"{c.task_success:.2f}", f"{c.overall():.2f}",
        ])
    per_scenario = tabulate(rows, headers=headers, tablefmt="github")

    # Aggregate per model.
    agg_rows = []
    for label in models:
        subset = [c for c in cards if c.model.startswith(f"{label}:")]
        if not subset:
            continue
        agg_rows.append([
            label,
            _avg([c.tool_accuracy for c in subset]),
            _avg([c.reasoning for c in subset]),
            _avg([c.error_recovery for c in subset if c.error_recovery is not None]),
            _avg([c.task_success for c in subset]),
            _avg([c.overall() for c in subset]),
        ])
    aggregate = tabulate(
        agg_rows,
        headers=["Model", "Tool", "Reason", "Recover", "Success", "Overall"],
        tablefmt="github",
    )

    md = (
        "# Model Comparison: Autonomous Rerouting Agent\n\n"
        "Trajectory-based evaluation. Each dimension is normalised to [0, 1]; "
        "`Overall` is a weighted composite (tool 0.30, reasoning 0.30, "
        "success 0.25, recovery 0.15 when applicable).\n\n"
        "## Aggregate (mean across scenarios)\n\n"
        f"{aggregate}\n\n"
        "## Per-scenario detail\n\n"
        f"{per_scenario}\n"
    )
    (RESULTS_DIR / "comparison.md").write_text(md)


def _avg(xs: list[float]) -> str:
    return f"{(sum(xs) / len(xs)):.2f}" if xs else "-"
