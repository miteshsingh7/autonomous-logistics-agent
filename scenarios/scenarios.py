"""Evaluation scenarios.

Each scenario is a telemetry alert plus an expectation the scorer checks
against. Two scenarios inject a one-time tool failure (`_fault`) so we can
measure error-recovery / self-healing behaviour, which is central to a
"proactive, self-healing" logistics system.
"""

from __future__ import annotations

from typing import Any

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "S1-weather-delay",
        "alert": {
            "shipment_id": "SHP-001",
            "description": "Severe winter storm on I-35 corridor; carrier "
                           "RoadRunner reporting 8h+ delay.",
            "severity": "high",
        },
        "expect": {
            "should_reroute": True,
            # A correct run touches these tools in a sensible order.
            "required_tools": ["get_telemetry", "find_alternatives", "execute_reroute"],
            "has_fault": False,
        },
    },
    {
        "id": "S2-breakdown",
        "alert": {
            "shipment_id": "SHP-002",
            "description": "Tractor mechanical failure outside Newark; shipment "
                           "stalled, high-value perishable cargo.",
            "severity": "high",
        },
        "expect": {
            "should_reroute": True,
            "required_tools": ["get_telemetry", "find_alternatives", "execute_reroute"],
            "has_fault": False,
        },
    },
    {
        "id": "S3-highvalue-air",
        "alert": {
            "shipment_id": "SHP-003",
            "description": "Port congestion in Seattle threatening a $90k "
                           "shipment SLA; multiple modes available.",
            "severity": "critical",
        },
        "expect": {
            "should_reroute": True,
            "required_tools": ["get_telemetry", "find_alternatives", "execute_reroute"],
            "has_fault": False,
        },
    },
    {
        "id": "S4-fault-alternatives",
        "alert": {
            "shipment_id": "SHP-001",
            "description": "Road closure on primary route; need alternatives now.",
            "severity": "high",
            # Inject a one-time failure the first time this tool is called.
            "_fault": {
                "tool": "find_alternatives",
                "message": "carrier marketplace timed out (503); retry advised",
            },
        },
        "expect": {
            "should_reroute": True,
            "required_tools": ["get_telemetry", "find_alternatives", "execute_reroute"],
            "has_fault": True,
        },
    },
    {
        "id": "S5-fault-telemetry",
        "alert": {
            "shipment_id": "SHP-003",
            "description": "Sensor gateway flapping; first telemetry read fails.",
            "severity": "medium",
            "_fault": {
                "tool": "get_telemetry",
                "message": "telemetry gateway unavailable (504); retry advised",
            },
        },
        "expect": {
            "should_reroute": True,
            "required_tools": ["get_telemetry", "find_alternatives", "execute_reroute"],
            "has_fault": True,
        },
    },
]


def get_scenarios() -> list[dict[str, Any]]:
    return SCENARIOS
