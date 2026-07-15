"""Mocked logistics tools.

These simulate the systems an autonomous rerouting agent would call in
production (telemetry bus, carrier marketplace, cost/ETA estimator, dispatch
API). They are deterministic so runs are reproducible, and they can raise
controlled failures so we can measure the agent's error-recovery behaviour.

The point of the assessment is workflow design + evaluation rigour, so no real
carrier APIs are involved.
"""

from __future__ import annotations

from typing import Any


class ToolError(Exception):
    """Raised by a tool to simulate a transient/backend failure."""


# --- static mock world -----------------------------------------------------

_SHIPMENTS: dict[str, dict[str, Any]] = {
    "SHP-001": {"origin": "Chicago, IL", "destination": "Dallas, TX",
                "carrier": "RoadRunner", "eta_hours": 22, "value_usd": 48000},
    "SHP-002": {"origin": "Newark, NJ", "destination": "Atlanta, GA",
                "carrier": "BlueLine", "eta_hours": 15, "value_usd": 12000},
    "SHP-003": {"origin": "Seattle, WA", "destination": "Denver, CO",
                "carrier": "PeakFreight", "eta_hours": 28, "value_usd": 90000},
}

_ALTERNATIVES: dict[str, list[dict[str, Any]]] = {
    "SHP-001": [
        {"carrier": "SwiftHaul", "route": "I-44 via Tulsa", "eta_hours": 24, "cost_usd": 1800, "reliability": 0.93},
        {"carrier": "RoadRunner", "route": "I-35 direct", "eta_hours": 30, "cost_usd": 1200, "reliability": 0.71},
        {"carrier": "AirBridge", "route": "air freight", "eta_hours": 9, "cost_usd": 7400, "reliability": 0.97},
    ],
    "SHP-002": [
        {"carrier": "BlueLine", "route": "I-95 reroute", "eta_hours": 19, "cost_usd": 900, "reliability": 0.88},
        {"carrier": "SwiftHaul", "route": "I-81 inland", "eta_hours": 17, "cost_usd": 1400, "reliability": 0.90},
    ],
    "SHP-003": [
        {"carrier": "PeakFreight", "route": "I-70 via Salt Lake", "eta_hours": 34, "cost_usd": 2100, "reliability": 0.80},
        {"carrier": "SummitRail", "route": "intermodal rail", "eta_hours": 40, "cost_usd": 1500, "reliability": 0.95},
        {"carrier": "AirBridge", "route": "air freight", "eta_hours": 12, "cost_usd": 9800, "reliability": 0.98},
    ],
}


# --- tool implementations ---------------------------------------------------

def get_telemetry(shipment_id: str) -> dict[str, Any]:
    """Return current status/telemetry for a shipment."""
    if shipment_id not in _SHIPMENTS:
        raise ToolError(f"unknown shipment_id '{shipment_id}'")
    base = _SHIPMENTS[shipment_id]
    return {"shipment_id": shipment_id, **base, "status": "in_transit"}


def find_alternatives(shipment_id: str) -> dict[str, Any]:
    """Return candidate carriers/routes for a shipment."""
    if shipment_id not in _ALTERNATIVES:
        raise ToolError(f"no alternatives catalogue for '{shipment_id}'")
    return {"shipment_id": shipment_id, "alternatives": _ALTERNATIVES[shipment_id]}


def estimate_cost_eta(shipment_id: str, carrier: str, route: str) -> dict[str, Any]:
    """Return a firm cost/ETA quote for a specific carrier+route option."""
    for alt in _ALTERNATIVES.get(shipment_id, []):
        if alt["carrier"] == carrier and alt["route"] == route:
            return {"shipment_id": shipment_id, "carrier": carrier, "route": route,
                    "eta_hours": alt["eta_hours"], "cost_usd": alt["cost_usd"],
                    "reliability": alt["reliability"]}
    raise ToolError(f"no quote for carrier='{carrier}' route='{route}'")


def execute_reroute(shipment_id: str, carrier: str, route: str) -> dict[str, Any]:
    """Commit the reroute with the dispatch system."""
    if shipment_id not in _SHIPMENTS:
        raise ToolError(f"cannot dispatch unknown shipment '{shipment_id}'")
    return {"shipment_id": shipment_id, "carrier": carrier, "route": route,
            "dispatch_id": f"DSP-{shipment_id[-3:]}-{carrier[:3].upper()}",
            "status": "rerouted"}


# Registry consumed by the graph + used to validate tool-calling accuracy.
TOOLS: dict[str, dict[str, Any]] = {
    "get_telemetry": {
        "fn": get_telemetry,
        "required_args": ["shipment_id"],
        "description": "Fetch current status/telemetry for a shipment.",
    },
    "find_alternatives": {
        "fn": find_alternatives,
        "required_args": ["shipment_id"],
        "description": "List candidate carriers and routes for a shipment.",
    },
    "estimate_cost_eta": {
        "fn": estimate_cost_eta,
        "required_args": ["shipment_id", "carrier", "route"],
        "description": "Get a firm cost/ETA/reliability quote for one option.",
    },
    "execute_reroute": {
        "fn": execute_reroute,
        "required_args": ["shipment_id", "carrier", "route"],
        "description": "Commit the chosen reroute to the dispatch system.",
    },
}


def tool_specs_for_prompt(names: list[str] | None = None) -> str:
    """Human-readable tool catalogue injected into a system prompt.

    Pass `names` to expose only a specialist agent's subset of tools; omit it
    to list the whole catalogue.
    """
    selected = names if names is not None else list(TOOLS.keys())
    lines = []
    for name in selected:
        spec = TOOLS.get(name)
        if not spec:
            continue
        args = ", ".join(spec["required_args"])
        lines.append(f"- {name}({args}): {spec['description']}")
    return "\n".join(lines)
