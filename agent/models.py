"""Model access layer.

Both the closed and open model are reached through a single OpenAI-compatible
endpoint (the Vercel AI Gateway), so swapping models is just changing the model
string. This keeps the comparison fair: identical prompts, identical harness,
identical parsing - only the weights change.

A deterministic MOCK model is included so the multi-agent trajectory harness can
be run and reviewed offline without API keys or token spend. The mock is
*role-aware*: it reads the "ROLE:" marker in the system prompt and plays that
specialist agent's policy.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

Message = dict[str, str]


class ModelClient:
    """Thin wrapper returning the raw text completion for a chat request."""

    def __init__(self, model: str, mock: bool | None = None) -> None:
        self.model = model
        self.mock = _env_flag("MOCK_MODE") if mock is None else mock
        self._client = None
        if not self.mock:
            from openai import OpenAI  # imported lazily so mock mode needs no key

            self._client = OpenAI(
                api_key=os.environ.get("AI_GATEWAY_API_KEY", ""),
                base_url=os.environ.get("AI_GATEWAY_BASE_URL",
                                        "https://ai-gateway.vercel.sh/v1"),
            )

    def complete(self, messages: list[Message]) -> str:
        if self.mock:
            return _mock_complete(self.model, messages)
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or "{}"


# --- deterministic mock policy ---------------------------------------------

def _mock_complete(model: str, messages: list[Message]) -> str:
    """Role-aware, fully deterministic stand-in for a specialist agent turn.

    Behaviour is derived purely from the message history, so it is reproducible.
    The "open" model is given slightly weaker tool discipline (it sometimes
    skips the firm-quote step in the planner) so the offline comparison table is
    non-trivial. This is ILLUSTRATIVE only - real differences come from live
    runs via `python run.py eval`.
    """
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    role = _detect_role(system)
    history = "\n".join(m["content"] for m in messages if m["role"] != "system")
    shipment_id = _find_shipment_id(history)
    is_open = "llama" in model.lower() or "open" in model.lower()

    # --- generic self-healing: retry the tool that ACTUALLY failed ----------
    failed = _last_unrecovered_error(messages)
    if failed is not None:
        tool_name, tool_args = failed
        if _error_count(messages, tool_name) >= 2:
            return json.dumps({
                "thought": f"'{tool_name}' failed repeatedly; escalating rather "
                           "than looping.",
                "handoff": {"action": "escalate",
                            "reason": f"{tool_name} unavailable"},
            })
        return json.dumps({
            "thought": f"The {tool_name} call failed with a transient error. "
                       "Retrying the same tool with corrected/identical args.",
            "tool": tool_name,
            "args": tool_args or {"shipment_id": shipment_id},
        })

    succeeded = _successful_tools(messages)

    if role == "assessor":
        return _assessor_turn(shipment_id, succeeded, history)
    if role == "planner":
        return _planner_turn(shipment_id, succeeded, messages, is_open)
    if role == "executor":
        return _executor_turn(shipment_id, succeeded, messages)

    # Unknown role: escalate safely.
    return json.dumps({"thought": "No role context; escalating.",
                       "handoff": {"action": "escalate", "reason": "no role"}})


def _assessor_turn(shipment_id: str, succeeded: set[str], history: str) -> str:
    if "get_telemetry" not in succeeded:
        return json.dumps({
            "thought": "First assess the shipment by pulling live telemetry.",
            "tool": "get_telemetry",
            "args": {"shipment_id": shipment_id},
        })
    severity = _severity(history)
    reroute_needed = severity in {"high", "critical", "medium"}
    return json.dumps({
        "thought": f"Telemetry confirms an at-risk shipment (severity={severity}). "
                   "A reroute is warranted." if reroute_needed
                   else "Impact is minor; no reroute needed.",
        "handoff": {"reroute_needed": reroute_needed,
                    "reason": f"severity={severity}"},
    })


def _planner_turn(shipment_id: str, succeeded: set[str],
                  messages: list[Message], is_open: bool) -> str:
    if "find_alternatives" not in succeeded:
        return json.dumps({
            "thought": "Enumerate alternative carriers/routes for the shipment.",
            "tool": "find_alternatives",
            "args": {"shipment_id": shipment_id},
        })

    best = _pick_best(_observations(messages))
    if best is None:
        return json.dumps({
            "thought": "No viable alternatives are available.",
            "handoff": {"selected": None, "reason": "no viable alternative"},
        })

    # The closed model confirms a firm quote before recommending; the open model
    # sometimes skips straight to the recommendation (weaker tool discipline).
    if "estimate_cost_eta" not in succeeded and not is_open:
        return json.dumps({
            "thought": f"Validate a firm quote for {best['carrier']} on cost/ETA "
                       "before recommending it.",
            "tool": "estimate_cost_eta",
            "args": {"shipment_id": shipment_id, "carrier": best["carrier"],
                     "route": best["route"]},
        })

    return json.dumps({
        "thought": f"{best['carrier']} has the best reliability-adjusted ETA "
                   f"({best['eta_hours']}h, ${best['cost_usd']}, "
                   f"reliability {best['reliability']}). Recommending it.",
        "handoff": {"selected": {"carrier": best["carrier"], "route": best["route"],
                                 "eta_hours": best["eta_hours"],
                                 "cost_usd": best["cost_usd"]},
                    "reason": "best reliability-adjusted ETA at reasonable cost"},
    })


def _executor_turn(shipment_id: str, succeeded: set[str],
                   messages: list[Message]) -> str:
    plan = _planned_selection(messages)
    if plan is None:
        return json.dumps({
            "thought": "No plan was provided; escalating.",
            "handoff": {"action": "escalate", "reason": "missing plan"},
        })
    if "execute_reroute" not in succeeded:
        return json.dumps({
            "thought": f"Committing the reroute via {plan['carrier']} "
                       f"on '{plan['route']}'.",
            "tool": "execute_reroute",
            "args": {"shipment_id": shipment_id, "carrier": plan["carrier"],
                     "route": plan["route"]},
        })
    return json.dumps({
        "thought": "Dispatch confirmed. Reroute complete.",
        "handoff": {"action": "rerouted", **plan},
    })


# --- helpers ----------------------------------------------------------------

def _detect_role(system: str) -> str:
    match = re.search(r"ROLE:\s*(assessor|planner|executor)", system)
    return match.group(1) if match else ""


def _pick_best(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the alternative with the best reliability-adjusted ETA."""
    alts: list[dict[str, Any]] = []
    for obs in observations:
        alts.extend(obs.get("alternatives", []))
    if not alts:
        return None
    return min(alts, key=lambda a: a["eta_hours"] / max(a["reliability"], 0.01)
               + a["cost_usd"] / 10000.0)


def _successful_tools(messages: list[Message]) -> set[str]:
    """Tools that returned a successful observation (ok=true)."""
    ok_tools: set[str] = set()
    pending: str | None = None
    for m in messages:
        if m["role"] == "assistant":
            pending = _tool_name(m["content"])
        elif m["role"] == "tool" and pending:
            data = _loads(m["content"])
            if isinstance(data, dict) and data.get("ok") is True:
                ok_tools.add(pending)
            pending = None
    return ok_tools


def _last_unrecovered_error(messages: list[Message]) -> tuple[str, dict[str, Any]] | None:
    """If the most recent tool result is an error, return its (tool, args).

    This is what makes self-healing retry the tool that ACTUALLY failed rather
    than a hard-coded one.
    """
    last_tool_idx = max((i for i, m in enumerate(messages) if m["role"] == "tool"),
                        default=-1)
    if last_tool_idx == -1:
        return None
    data = _loads(messages[last_tool_idx]["content"])
    if not (isinstance(data, dict) and data.get("ok") is False):
        return None
    # Walk back to the assistant tool call that produced this result.
    for j in range(last_tool_idx - 1, -1, -1):
        if messages[j]["role"] == "assistant":
            call = _loads(messages[j]["content"])
            if isinstance(call, dict) and call.get("tool"):
                return call["tool"], call.get("args", {})
            break
    return None


def _error_count(messages: list[Message], tool_name: str) -> int:
    count = 0
    pending: str | None = None
    for m in messages:
        if m["role"] == "assistant":
            pending = _tool_name(m["content"])
        elif m["role"] == "tool" and pending == tool_name:
            data = _loads(m["content"])
            if isinstance(data, dict) and data.get("ok") is False:
                count += 1
            pending = None
    return count


def _planned_selection(messages: list[Message]) -> dict[str, Any] | None:
    """Recover the planner's selection injected into the executor briefing."""
    for m in messages:
        if m["role"] != "user":
            continue
        match = re.search(r"PLAN=(\{.*\})", m["content"])
        if match:
            data = _loads(match.group(1))
            if isinstance(data, dict) and data.get("carrier"):
                return data
    return None


def _observations(messages: list[Message]) -> list[dict[str, Any]]:
    obs: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] != "tool":
            continue
        data = _loads(m["content"])
        if isinstance(data, dict) and data.get("ok") is not False:
            obs.append(data)
    return obs


def _tool_name(content: str) -> str | None:
    data = _loads(content)
    return data.get("tool") if isinstance(data, dict) else None


def _severity(text: str) -> str:
    match = re.search(r"severity=(\w+)", text)
    return match.group(1) if match else "unknown"


def _find_shipment_id(text: str) -> str:
    match = re.search(r"SHP-\d{3}", text)
    return match.group(0) if match else "SHP-001"


def _loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "0").strip() in {"1", "true", "True", "yes"}
