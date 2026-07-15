# Decision & AI-Tool Usage Log

This log documents (a) the key engineering/architecture decisions and their
rationale, and (b) how AI tooling was used during the assessment, as required.

## Architecture decisions

| Decision | Chosen | Why | Alternatives considered |
|----------|--------|-----|-------------------------|
| Agent framework | **LangGraph** | Explicit, inspectable graph state; natural home for the self-healing back-edge; the graph *is* the trajectory we evaluate | CrewAI (less state control), AutoGen (conversation-centric), hand-rolled loop |
| Agent topology | **Multi-agent (supervisor + assessor/planner/executor)** | Narrow per-agent tool surface improves tool-calling reliability; hand-off artifacts are inspectable; each agent self-heals on its own tools; enables tiered open/closed routing | Single monolithic ReAct loop (larger tool surface, coarser control) |
| Self-healing | **Retry the tool that actually failed** | The retry reads the last errored tool from history and re-issues *that* call (with an escalation cap), rather than a hard-coded tool | Hard-coded retry target (incorrect when a different tool fails) |
| Deck format | **Real .pptx generated from results** | Assignment asks for a presentation deck; `scripts/build_deck.py` renders `deck/presentation.pptx` and pulls live scorecard numbers | Markdown-only outline (not a deliverable deck) |
| Eval philosophy | **Trajectory-based** | Autonomous logistics cares about *process*, not just outcome; catches unsafe reasoning an outcome benchmark misses | Outcome-only accuracy |
| Scoring | **Hybrid rule-based + LLM-as-judge** | Rules give objective/reproducible anchors; judge captures subjective reasoning quality | Pure rules (blind to reasoning), pure judge (noisy, unauditable) |
| Model access | **Single OpenAI-compatible gateway** | Same prompt/harness for both models = fair comparison; one-line model swap | Two separate SDKs (unfair, more code) |
| Tools | **Mocked, deterministic, fault-injectable** | Assessment is about workflow + eval rigour, not carrier integrations; determinism = reproducible scores; injected faults = measurable recovery | Live carrier APIs (out of scope, non-deterministic) |
| Offline mode | **Deterministic mock model** | Reviewer can run the full harness with no keys/tokens; open model given slightly weaker discipline so the table is non-trivial | Require live keys to run anything |

## Model selection

- **Closed:** `anthropic/claude-sonnet-4` — strong agentic/tool-use frontier model.
- **Open:** `meta/llama-3.3-70b-instruct` — widely deployed, self-hostable open model.
- Both are reachable through the Vercel AI Gateway with zero provider-specific code.

## How AI tools were used

- **Code generation assistant (v0):** used to scaffold the LangGraph agent, the
  scoring harness, and the report/deck drafts. All generated code was reviewed,
  run, and verified end-to-end (`python run.py eval --mock` produces the tables in
  the report; `python run.py trace ...` was used to inspect trajectories).
- **What I directed vs. what the tool produced:** I specified the trajectory-based
  methodology, the four scoring dimensions and their weights, the scenario design
  (including the two injected-fault cases), and the tiered production
  recommendation. The tool produced boilerplate (graph wiring, argument parsing,
  table formatting) and a first draft of prose that I edited for accuracy.
- **Verification:** the harness was executed; the trajectory trace was manually
  inspected to confirm the self-healing loop fires on injected faults and that
  tool-calling order is enforced by the scorer.

## Known shortcuts (called out honestly)

- Mock-mode numbers are illustrative; live-model numbers require a gateway key.
- The open model's "weaker discipline" in mock mode is hard-coded for
  demonstration — it is **not** a claim about the real model's quality. Run live
  to get real figures.
- Five scenarios prove the method; they are not a powered benchmark.
