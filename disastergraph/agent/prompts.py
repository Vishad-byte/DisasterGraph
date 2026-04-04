from __future__ import annotations

import json
from typing import Any


def build_assignment_prompt(
    zone: dict[str, Any],
    victims: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    route: dict[str, Any],
) -> str:
    zone_name = zone.get("name", zone.get("id", "unknown-zone"))
    zone_id = zone.get("id", "unknown-zone")
    severity = zone.get("disaster_severity", 0)
    route_path = route.get("path", ["unknown"])
    total_time = route.get("total_time", 0)

    return f"""
You are an emergency response coordinator.

Disaster zone: {zone_name} ({zone_id}) - severity {severity}
Top 5 victims by urgency: {json.dumps(victims[:5], ensure_ascii=True)}
Available resources: {json.dumps(resources[:3], ensure_ascii=True)}
Optimal route: {json.dumps(route_path, ensure_ascii=True)} - ETA {total_time} min

Generate a specific dispatch order. Output JSON only:
{{
  "resource_id": "string",
  "resource_type": "string",
  "destination_zone": "string",
  "top_victim_id": "string",
  "priority_victim_count": 0,
  "route_description": "string",
  "action": "string",
  "alert_message": "string",
  "eta": 0
}}
""".strip()
