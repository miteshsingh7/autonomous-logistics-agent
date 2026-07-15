"""CLI entrypoint.

Examples
--------
Run the full head-to-head evaluation (offline, no keys needed):
    python run.py eval --mock

Run against live models via the AI Gateway:
    python run.py eval

Run a single scenario for one model and print its trajectory:
    python run.py trace --scenario S4-fault-alternatives --model open --mock
"""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

load_dotenv(".env.development.local")
load_dotenv()  # .env fallback


def _models() -> dict[str, str]:
    return {
        "closed": os.environ.get("CLOSED_MODEL", "anthropic/claude-sonnet-4"),
        "open": os.environ.get("OPEN_MODEL", "meta/llama-3.3-70b-instruct"),
    }


def cmd_eval(args: argparse.Namespace) -> None:
    from eval import run_all

    mock = True if args.mock else None
    cards = run_all(_models(), mock=mock)
    print(f"\nScored {len(cards)} trajectories.")
    print("See results/comparison.md and results/scorecards.json\n")

    comparison = os.path.join("results", "comparison.md")
    if os.path.exists(comparison):
        with open(comparison, encoding="utf-8") as fh:
            print(fh.read())


def cmd_trace(args: argparse.Namespace) -> None:
    from agent import build_agent
    from scenarios import get_scenarios

    mock = True if args.mock else None
    model = _models()[args.model]
    scenario = next((s for s in get_scenarios() if s["id"] == args.scenario), None)
    if scenario is None:
        raise SystemExit(f"unknown scenario '{args.scenario}'")

    agent = build_agent(model, mock=mock)
    state = agent.invoke(
        {"scenario_id": scenario["id"], "alert": scenario["alert"],
         "model": model, "max_steps": 8},
        config={"recursion_limit": 50},
    )
    print(f"\n=== {scenario['id']}  |  {args.model} ({model}) ===\n")
    for i, step in enumerate(state["trajectory"], 1):
        tag = step.kind.upper()
        who = step.agent or "-"
        detail = step.tool or ""
        extra = f" {json.dumps(step.args)}" if step.args else ""
        print(f"{i:>2}. ({who:<12}) [{tag}] {detail}{extra}")
        if step.content:
            print(f"       {step.content[:200]}")
    print(f"\nDECISION: {json.dumps(state.get('decision', {}), indent=2)}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous rerouting agent + eval")
    sub = parser.add_subparsers(dest="command", required=True)

    p_eval = sub.add_parser("eval", help="run full head-to-head evaluation")
    p_eval.add_argument("--mock", action="store_true", help="offline deterministic run")
    p_eval.set_defaults(func=cmd_eval)

    p_trace = sub.add_parser("trace", help="print one scenario's trajectory")
    p_trace.add_argument("--scenario", required=True)
    p_trace.add_argument("--model", choices=["closed", "open"], default="closed")
    p_trace.add_argument("--mock", action="store_true")
    p_trace.set_defaults(func=cmd_trace)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
