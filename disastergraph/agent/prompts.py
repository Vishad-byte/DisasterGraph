from __future__ import annotations

import json
from typing import Any


def build_assignment_prompt(
    zone: dict[str, Any],
    victims: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    route: dict[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> str:
    zone_name = zone.get("name", zone.get("id", "unknown-zone"))
    zone_id = zone.get("id", "unknown-zone")
    severity = zone.get("disaster_severity", 0)

    # Format verified candidate facts if provided
    candidates_text = ""
    candidate_ids = []
    if candidates:
        candidate_lines = []
        for idx, cand in enumerate(candidates, start=1):
            c_id = str(cand.get("resource_id", ""))
            candidate_ids.append(c_id)
            name = cand.get("resource_name", c_id)
            eta = cand.get("eta_min", 15.0)
            dist = cand.get("distance_km", 0.0)
            extra = cand.get("extra_minutes", 0.0)
            uses_hazard = cand.get("uses_hazard_road", False)
            no_safe = cand.get("no_safe_route", False)

            hazard_note = "Clear route (no hazard influence)"
            if no_safe:
                hazard_note = "NO SAFE ROUTE: all corridors pass closed roads"
            elif uses_hazard:
                hazard_note = f"Avoids hazard corridor (+{extra:.1f} min safety detour)"

            candidate_lines.append(
                f"- Candidate {idx}: ID '{c_id}' ({name}) | Verified ETA: {eta:.1f} min | Distance: {dist:.1f} km | Safety: {hazard_note}"
            )
        candidates_text = "\n".join(candidate_lines)

    route_info = ""
    if route:
        route_path = route.get("path", ["unknown"])
        total_time = route.get("total_time", 0)
        route_info = f"Macro Corridor: {json.dumps(route_path, ensure_ascii=True)} - Base ETA: {total_time} min"

    if candidates_text:
        routing_facts_text = candidates_text
    else:
        res_json = json.dumps(resources[:3], ensure_ascii=True)
        routing_facts_text = f"Available resources: {res_json}\n{route_info}"

    allowed_ids_str = ", ".join(f"'{cid}'" for cid in candidate_ids) if candidate_ids else "None (use resources)"
    victims_json = json.dumps(victims[:5], ensure_ascii=True)

    return f"""
You are an emergency response coordinator dispatching life-saving resources.

DISASTER CONTEXT:
- Impacted Zone: {zone_name} ({zone_id}) - Severity Score: {severity}
- Priority Victims in Zone: {victims_json}

DETERMINISTIC ROUTING FACTS (Verified by NetworkX/OSM road graph - DO NOT recalculate or modify these numbers):
{routing_facts_text}

CRITICAL RULES:
1. You MUST select 'resource_id' from the verified candidate IDs: [{allowed_ids_str}]
2. Do NOT invent new resource IDs, road names, or ETAs. Use the exact ETA and ID from your chosen candidate.
3. Write a concise, professional 1-line justification in 'route_description' explaining why this candidate was chosen (e.g., "Ambulance 3 chosen: lowest ETA (12.4m) while completely bypassing toxic NO2 plume in Ward 002").

Output JSON only in this exact format:
{{
  "resource_id": "string (MUST be one of [{allowed_ids_str}])",
  "resource_type": "ambulance",
  "destination_zone": "{zone_id}",
  "top_victim_id": "string",
  "priority_victim_count": 1,
  "route_description": "string",
  "action": "Dispatch emergency team immediately via verified safe route",
  "alert_message": "string",
  "eta": 0.0
}}
""".strip()
