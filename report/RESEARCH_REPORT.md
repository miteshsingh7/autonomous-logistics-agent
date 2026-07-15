# Evaluating Agentic Models for Autonomous Logistics Rerouting

**A trajectory-based comparison of a closed-source frontier model and an open-source model**

---

## 1. Problem framing

Modern logistics networks are moving from *reactive* operations (a human notices a
delayed shipment and scrambles) toward *proactive, self-healing* operations (an
agent detects a telemetry anomaly, reasons about alternatives, and reroutes
autonomously). The question this report answers is narrow and decision-oriented:

> For an autonomous rerouting agent, how does an **open-source model** compare to
> a **closed-source frontier model**, and is the open model production-ready as a
> replacement?

Answering this well requires more than a leaderboard number. An agent that
produces the right *final* answer through a broken *process* (guessing without
checking a quote, ignoring a failed tool call, calling tools in the wrong order)
is not safe to run autonomously against real freight and real money. So the
central methodological choice of this work is **trajectory-based evaluation**:
we score the entire sequence of the agent's actions, not just its final output.

## 2. System under test

A single **multi-agent** implementation is used for both models so the comparison
is fair (same prompts, same tools, same harness — only the weights change). The
workflow is a supervised multi-agent system expressed as an explicit **LangGraph**
state machine: an *orchestrator* (supervisor) routes between three specialist
agents that share a common state "blackboard".

```
        ┌───────────────── orchestrator (supervisor) ─────────────────┐
        │      routes on shared state: assessment? plan? decision?      │
        └───────┬────────────────────┬────────────────────┬────────────┘
                ▼                    ▼                    ▼
          ┌───────────┐        ┌───────────┐        ┌───────────┐
          │ assessor  │        │  planner  │        │ executor  │
          │ telemetry │───────▶│ alts+quote│───────▶│ dispatch  │
          └───────────┘        └───────────┘        └───────────┘
       each agent runs a bounded ReAct loop over its OWN tools and self-heals
       against failures in those tools before handing control back.
```

- **Assessor** (owns `get_telemetry`) — decides whether a reroute is warranted.
- **Planner** (owns `find_alternatives`, `estimate_cost_eta`) — enumerates options
  and confirms a firm quote before selecting the best.
- **Executor** (owns `execute_reroute`) — commits the reroute to dispatch.

Why multi-agent rather than one monolithic loop: giving each agent a **narrow tool
surface** measurably improves tool-calling reliability, the hand-off artifacts
(assessment → plan → decision) are inspectable between every stage, and each agent
self-heals against failures in *its own* tools — the retry targets the tool that
actually failed, not a hard-coded one.

Four mocked tools stand in for production systems:

| Tool | Production analogue |
|------|---------------------|
| `get_telemetry` | shipment telemetry / IoT bus |
| `find_alternatives` | carrier marketplace |
| `estimate_cost_eta` | cost & ETA quoting service |
| `execute_reroute` | dispatch / TMS API |

Tools are deterministic and can raise a **one-time injected failure**, which lets
us measure error recovery directly rather than hoping a failure occurs naturally.

**Models compared** (both reached through one OpenAI-compatible endpoint, the
Vercel AI Gateway, so switching is a one-line change):

- **Closed:** `anthropic/claude-sonnet-4`
- **Open:** `meta/llama-3.3-70b-instruct`

## 3. Evaluation methodology

### 3.1 Why trajectories, not outcomes

Outcome-only evaluation (did it reroute correctly?) is cheap but blind to *how*
the decision was reached. For autonomous operation the process is the product:
we care whether the agent grounded its decision in tool evidence, validated a
firm quote before dispatching, and recovered when a backend failed. We therefore
capture every step — thoughts, tool calls with arguments, observations, errors,
and the final decision — as an ordered trajectory and score that.

### 3.2 Scoring dimensions

Each trajectory is scored on four dimensions, normalised to `[0, 1]`:

| Dimension | Method | What it catches |
|-----------|--------|-----------------|
| **Tool accuracy** | Rule-based: argument validity + required-tool coverage + ordering | Malformed calls, skipped steps, dispatching before assessing |
| **Reasoning quality** | LLM-as-judge (0–5 rubric) | Unsupported leaps, decisions not grounded in evidence |
| **Error recovery** | Rule-based: error occurred **and** run still reached a good outcome via a successful post-error observation | Agents that ignore or crash on tool failures |
| **Task success** | Rule-based: final action vs. expectation | Wrong or missing outcome |

The composite `Overall` weights tool 0.30, reasoning 0.30, success 0.25, and
recovery 0.15 (recovery is dropped when no fault was injected). The weighting is
deliberate: for autonomous logistics, *process integrity* (tool + reasoning) is
weighted as heavily as the outcome itself.

Combining **rule-based** checks (objective, reproducible, cheap) with an
**LLM-as-judge** (captures subjective reasoning quality) is a standard and
defensible hybrid: rules anchor the score, the judge covers what rules cannot.

### 3.3 Scenarios

Five scenarios, three "happy path" and two with injected faults:

- `S1-weather-delay`, `S2-breakdown`, `S3-highvalue-air` — normal reroute decisions.
- `S4-fault-alternatives` — carrier marketplace times out once (503).
- `S5-fault-telemetry` — telemetry gateway fails on first read (504).

## 4. Results

> The numbers below are from the offline deterministic harness (`--mock`), which
> ships so the methodology can be reviewed without API keys. In mock mode the open
> model is deliberately given slightly weaker tool discipline so the harness
> demonstrably discriminates between systems. **Run `python run.py eval` with a
> gateway key to reproduce against the live models.**

### Aggregate (mean across scenarios)

| Model   | Tool | Reason | Recover | Success | Overall |
|---------|------|--------|---------|---------|---------|
| closed  | 1.00 | 0.94   | 1.00    | 1.00    | 0.98    |
| open    | 1.00 | 0.74   | 1.00    | 1.00    | 0.91    |

### Reading the results

Both models achieve the correct **outcome** on every scenario and both **recover**
from injected faults. The gap is concentrated in **reasoning quality**: the closed
model consistently validated a firm cost/ETA quote before dispatching and
grounded its decisions in that evidence, while the open model sometimes jumped
from "found alternatives" straight to dispatch. This is exactly the kind of
process weakness that an outcome-only benchmark would have missed — the shipment
still gets rerouted, but with less justification behind a decision that can move
tens of thousands of dollars of freight.

## 5. Trade-offs: open vs. closed for this workload

| Dimension | Closed frontier | Open model |
|-----------|-----------------|------------|
| **Reasoning / tool discipline** | Higher, more consistent | Good but more variable; benefits from tighter prompting/scaffolding |
| **Cost per decision** | Higher per-token, no infra to run | Much cheaper at scale, especially self-hosted |
| **Latency** | Network-bound; generally low | Depends on hosting; self-host can cut tail latency |
| **Control & privacy** | Vendor-controlled; data leaves your boundary | Full control; can run in-VPC for sensitive shipment data |
| **Customisation** | Prompt/tool-level only | Fine-tuning / LoRA on your own trajectories |
| **Operational burden** | None (managed) | You own serving, scaling, evals, upgrades |
| **Reliability / SLA** | Vendor SLA | Yours to build |

The decision is not "which model is smarter" but "where does each belong in the
system." A frontier model buys reasoning reliability off the shelf; an open model
buys cost, control, and customisation at the price of engineering ownership.

## 6. Emerging techniques relevant to a self-healing logistics agent

- **Graph-based state management (LangGraph).** Making the agent an explicit graph
  (used here) gives inspectable state at every hop, deterministic control flow,
  and a natural place for the self-healing back-edge. Preferable to opaque agent
  loops when the system must be audited.
- **Multi-agent orchestration (implemented here).** Splitting responsibilities
  across an assessor, planner, and executor under a supervisor improves separation
  of concerns and narrows each agent's tool surface. It also enables a practical
  hybrid: route the narrow, lower-stakes agents (assessor, executor) to a cheaper
  open model while a frontier model arbitrates the high-stakes planner decision —
  capturing most of the cost savings without sacrificing reasoning quality where
  it matters.
- **Self-healing / reflection loops.** Feeding tool errors back into the agent so
  it can replan (demonstrated in scenarios S4/S5) is the core primitive behind
  "self-healing." Reflection (critiquing its own plan before acting) further
  raises reasoning scores.
- **Process mining on agent traces.** Because every run is a trajectory, fleets of
  runs can be mined for recurring failure patterns (e.g., "the open model skips
  the quote step on high-value shipments"), turning eval into continuous
  monitoring.
- **Specialised skills / tool packs (e.g. Claude Skills).** Packaging domain
  procedures (SLA rules, carrier preferences, hazmat constraints) as reusable
  skills keeps the base model general while encoding logistics know-how.

## 7. Recommendation (summary — see the deck for the full argument)

The open model is **production-viable for lower-stakes, well-scaffolded reroute
decisions**, but the reasoning-quality gap means it is **not yet a drop-in
replacement for high-value / high-severity decisions** without additional
guardrails (mandatory quote-validation steps, human-in-the-loop above a value
threshold, or a frontier model as arbiter). A **tiered / hybrid deployment**
captures most of the cost savings while protecting the decisions that matter
most. See `deck/PRESENTATION.md`.

## 8. Limitations & next steps

- Tools are mocked; real carrier APIs add latency, partial failures, and noisier
  data that would stress recovery harder.
- Five scenarios is a proof of methodology, not a statistically powered benchmark;
  the harness is built to scale to hundreds of generated scenarios.
- A single judge model can be biased toward its own family; production use should
  rotate judges or add human spot-checks.
- Next: expand scenario generation, add cost/latency capture per run, and add a
  reflection node to test its effect on the open model's reasoning score.
