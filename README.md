# Autonomous Logistics Rerouting Agent + Trajectory Evaluation

AI Researcher take-home assessment. One codebase powers all three deliverables:

1. **Technical POC** — a LangGraph multi-agent workflow that autonomously reroutes
   shipments (`agent/`).
2. **Research & evaluation** — a trajectory-based eval harness comparing a
   closed-source and an open-source model (`eval/`, `report/`).
3. **Product strategy** — a go/no-go production recommendation
   (`deck/PRESENTATION.md`).

> Design principle: the agent is built **once**, both models run through the same
> harness, and the head-to-head result drives the recommendation. See
> `report/RESEARCH_REPORT.md` and `DECISIONS.md`.

## Quick start (offline, no API key)

```bash
# 1. create env + install
uv venv .venv && source .venv/bin/activate     # or: python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. run the full head-to-head evaluation with the deterministic mock model
python run.py eval --mock

# 3. inspect a single trajectory (watch the self-healing recovery on S4/S5)
python run.py trace --scenario S4-fault-alternatives --model open --mock
```

Outputs land in `results/`:
- `results/comparison.md` — aggregate + per-scenario tables
- `results/scorecards.json` — every scorecard
- `results/raw/*.json` — full captured trajectories

## Running against live models

Both models are reached through one OpenAI-compatible endpoint (the Vercel AI
Gateway), so switching models is a one-line env change.

```bash
cp .env.example .env
# edit .env: set AI_GATEWAY_API_KEY, and optionally CLOSED_MODEL / OPEN_MODEL
python run.py eval        # no --mock => live models
```

## Repository layout

```
agent/          LangGraph multi-agent rerouting workflow
  state.py        shared "blackboard" state + trajectory primitives
  tools.py        mocked logistics tools (fault-injectable)
  models.py       one gateway client for both models + role-aware offline mock
  graph.py        orchestrator + assessor/planner/executor specialist agents
eval/
  scoring.py      4-dimension trajectory scoring (rules + LLM-as-judge)
  runner.py       runs both models over all scenarios, writes tables
scenarios/        5 scenarios (3 normal, 2 with injected faults)
scripts/
  build_deck.py   generates deck/presentation.pptx from the latest scorecards
report/           RESEARCH_REPORT.md (methodology, results, trade-offs)
deck/             presentation.pptx (generated) + PRESENTATION.md (outline)
results/          generated evaluation artifacts
run.py            CLI: `eval` and `trace`
DECISIONS.md      architecture decisions + AI-tool usage log
```

## Multi-agent architecture

An orchestrator (supervisor) routes between three specialist agents over a shared
state blackboard. Each agent owns a narrow tool set and self-heals against
failures in its own tools:

```
orchestrator ─► assessor (get_telemetry)
            ─► planner  (find_alternatives, estimate_cost_eta)
            ─► executor (execute_reroute)
```

## Regenerate the slide deck

```bash
python scripts/build_deck.py     # writes deck/presentation.pptx (uses live scorecards if present)
```

## Putting this on GitHub

```bash
git init && git add . && git commit -m "Autonomous rerouting agent + trajectory eval"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
```

## Evaluation approach in one paragraph

We score the agent's **full trajectory** (every thought, tool call, observation,
error, and the final decision) rather than only the final answer, because for
autonomous logistics the *process* is the product. Four dimensions —
**tool accuracy** (rules), **reasoning quality** (LLM-as-judge), **error recovery**
(via one-time fault injection), and **task success** (rules) — combine into a
weighted composite that favours process integrity. Full write-up in
`report/RESEARCH_REPORT.md`.
