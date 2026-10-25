from __future__ import annotations

import importlib
import json
import logging
import asyncio
import time
from dataclasses import dataclass
from typing import Any

from disastergraph.agent.llm_client import LLMClient
from disastergraph.agent.prompts import build_assignment_prompt
from disastergraph.config import get_neo4j_connection, get_settings
from disastergraph.graph.router import RoadRouter, ComputedRoute


LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@dataclass(slots=True)
class Assignment:
    resource_id: str
    resource_type: str
    destination_zone: str
    top_victim_id: str
    priority_victim_count: int
    route_description: str
    action: str
    alert_message: str
    eta: float


class DisasterGraphAgent:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.conn = get_neo4j_connection(self.settings)
        self.llm = LLMClient(self.settings)
        self.bot: Any | None = None
        try:
            telegram_mod = importlib.import_module("telegram")
            if self.settings.telegram_token:
                self.bot = telegram_mod.Bot(token=self.settings.telegram_token)
        except Exception as exc:
            LOGGER.warning("Telegram bot initialization skipped: %s", exc)
        self.officer_chat_ids = self._load_officer_chat_ids()
        self.router = RoadRouter.get_instance(self.settings)

        if self.settings.llm_provider == "claude" and not self.settings.claude_api_key:
            LOGGER.warning("CLAUDE_API_KEY is empty; assignment generation will fail.")
        if self.settings.llm_provider == "openrouter" and not self.settings.openrouter_api_key:
            LOGGER.warning("OPENROUTER_API_KEY is empty; assignment generation will fail.")
        if not self.settings.telegram_token:
            LOGGER.warning("TELEGRAM_TOKEN is empty; alert delivery will fail.")

    def _load_officer_chat_ids(self) -> dict[str, str]:
        mapping: dict[str, str] = {}

        officers = self.conn.getVertices("Officer", limit=10000)
        for officer in officers:
            if not isinstance(officer, dict):
                continue
            attrs = officer.get("attributes", {})
            if not isinstance(attrs, dict):
                continue
            zone_id = str(attrs.get("zone", "")).strip()
            chat_id = str(attrs.get("telegram_chat_id", "")).strip()
            if zone_id and chat_id:
                mapping[zone_id] = chat_id

        try:
            data = json.loads(self.settings.officer_chat_ids_json)
            if isinstance(data, dict):
                mapping.update({str(k): str(v) for k, v in data.items()})
        except json.JSONDecodeError:
            LOGGER.warning("Invalid OFFICER_CHAT_IDS JSON; falling back to empty mapping.")
        return mapping

    def _extract_event_ids(self) -> list[str]:
        rows = self.conn.getVertices("DisasterEvent", select="status", limit=500)
        event_ids: list[str] = []
        for row in rows:
            attrs = row.get("attributes", {}) if isinstance(row, dict) else {}
            status = attrs.get("status", "")
            if status != "active":
                continue
            event_id = row.get("v_id") if isinstance(row, dict) else None
            if event_id:
                event_ids.append(str(event_id))
        return event_ids

    def _extract_active_hazards(self) -> list[dict[str, Any]]:
        hazards: list[dict[str, Any]] = []
        if hasattr(self.conn, "run_query"):
            try:
                cypher = (
                    "MATCH (d:DisasterEvent {status: 'active'}) "
                    "OPTIONAL MATCH (d)-[:AFFECTS]->(z:Zone) "
                    "RETURN properties(d) as d, collect(z.zone_id) as zones, "
                    "avg(z.centroid_lat) as avg_lat, avg(z.centroid_lng) as avg_lng"
                )
                rows = self.conn.run_query(cypher)
                for r in rows:
                    d_props = r.get("d") or {}
                    lat = d_props.get("lat") or d_props.get("latitude") or r.get("avg_lat")
                    lng = d_props.get("lng") or d_props.get("longitude") or d_props.get("lon") or r.get("avg_lng")
                    if lat is not None and lng is not None:
                        hazards.append({
                            "event_id": str(d_props.get("event_id") or ""),
                            "event_type": str(d_props.get("event_type") or "hazard"),
                            "severity": float(d_props.get("severity") or 0.5),
                            "lat": float(lat),
                            "lng": float(lng),
                            "radius_meters": float(d_props.get("radius_meters") or 1500.0),
                        })
            except Exception as exc:
                LOGGER.warning("Could not query active hazards via run_query: %s", exc)
        return hazards

    def _zone_centroid(self, zone_id: str) -> tuple[float, float]:
        if hasattr(self.conn, "run_query"):
            try:
                rows = self.conn.run_query(
                    "MATCH (z:Zone {zone_id: $zid}) RETURN z.centroid_lat as lat, z.centroid_lng as lng",
                    {"zid": zone_id},
                )
                if rows and rows[0].get("lat") is not None and rows[0].get("lng") is not None:
                    return (float(rows[0]["lat"]), float(rows[0]["lng"]))
            except Exception:
                pass
        return (28.6139, 77.2090)

    def run_detection_cycle(self) -> None:
        event_ids = self._extract_event_ids()
        LOGGER.info("Active events found: %s", len(event_ids))

        # Synchronize active hazards with RoadRouter
        active_hazards = self._extract_active_hazards()
        self.router.update_hazard_penalties(active_hazards)

        for event_id in event_ids:
            affected_resp = self.conn.runInstalledQuery(
                "findAffectedZones",
                {"event_id": event_id},
            )
            affected_zones = self._normalize_vertex_rows(affected_resp, expected_key="zones")

            for zone in affected_zones:
                zone_id = str(zone.get("zone_id") or zone.get("id") or zone.get("v_id") or "")
                if not zone_id:
                    continue

                victims_resp = self.conn.runInstalledQuery(
                    "rankVictimsByUrgency",
                    {"zone_id": zone_id},
                )
                victims = self._normalize_vertex_rows(victims_resp, expected_key="victims")
                if not victims:
                    continue

                resources_resp = self.conn.runInstalledQuery(
                    "findAvailableResources",
                    {
                        "zone_id": zone_id,
                        "resource_type": "ambulance",
                    },
                )
                resources = self._normalize_vertex_rows(resources_resp, expected_key="resources")
                if not resources:
                    continue

                # Target victim location for precise road routing
                first_victim = victims[0]
                target_lat = float(first_victim.get("location_lat") or zone.get("centroid_lat") or 28.6139)
                target_lng = float(first_victim.get("location_lng") or zone.get("centroid_lng") or 77.2090)

                # Precompute deterministic road routes & ETAs for top candidate ambulances
                candidates: list[dict[str, Any]] = []
                for res in resources[:5]:
                    r_id = str(res.get("id") or res.get("res_id") or "")
                    r_lat = res.get("location_lat")
                    r_lng = res.get("location_lng")
                    if r_lat is None or r_lng is None:
                        r_zone_id = self._resource_zone(res, fallback_zone=zone_id)
                        r_lat, r_lng = self._zone_centroid(r_zone_id)

                    route_calc = self.router.compute_route(
                        float(r_lat), float(r_lng), target_lat, target_lng
                    )
                    candidates.append(
                        {
                            "resource_id": r_id,
                            "resource_name": str(res.get("name") or f"Ambulance {r_id}"),
                            "resource_type": "ambulance",
                            "eta_min": route_calc.eta_min,
                            "distance_km": route_calc.distance_km,
                            "fastest_eta_min": route_calc.fastest_eta_min,
                            "extra_minutes": route_calc.extra_minutes,
                            "uses_hazard_road": route_calc.uses_hazard_road,
                            "hazard_zones_crossed": route_calc.hazard_zones_crossed,
                            "no_safe_route": route_calc.no_safe_route,
                            "route": route_calc,
                            "resource_raw": res,
                        }
                    )

                # Sort candidates: prioritize safe routes, then lowest ETA
                candidates.sort(key=lambda c: (1 if c["no_safe_route"] else 0, c["eta_min"]))

                first_resource = candidates[0]["resource_raw"]
                resource_zone = self._resource_zone(first_resource, fallback_zone=zone_id)
                route_resp = self.conn.runInstalledQuery(
                    "shortestPassableRoute",
                    {
                        "from_zone": resource_zone,
                        "to_zone": zone_id,
                    },
                )
                route = self._normalize_route(route_resp, resource_zone, zone_id)

                if self.settings.agent_verbose:
                    LOGGER.info(
                        "Event %s zone %s: victims=%s candidates=%s",
                        event_id,
                        zone_id,
                        len(victims),
                        len(candidates),
                    )

                assignment = self.generate_assignment(
                    zone, victims, resources, route, candidates=candidates
                )
                if not assignment.resource_id or not assignment.top_victim_id:
                    continue

                # Match candidate chosen by agent (or fallback to top candidate)
                chosen_cand = next(
                    (c for c in candidates if c["resource_id"] == assignment.resource_id),
                    candidates[0],
                )
                chosen_route = chosen_cand["route"]
                chosen_res_raw = chosen_cand["resource_raw"]

                if self.settings.agent_verbose:
                    LOGGER.info(
                        "Verified assignment: resource=%s, real_eta=%.1fm, distance=%.1fkm, hazard_safe=%s",
                        assignment.resource_id,
                        chosen_route.eta_min,
                        chosen_route.distance_km,
                        not chosen_route.uses_hazard_road,
                    )

                res_cap = int(chosen_res_raw.get("capacity", 0))
                curr_load = int(chosen_res_raw.get("current_load", 0))
                new_load = min(res_cap, curr_load + max(1, assignment.priority_victim_count))

                self.conn.runInstalledQuery(
                    "updateResourceState",
                    {
                        "res_id": assignment.resource_id,
                        "new_load": new_load,
                        "assigned_person": assignment.top_victim_id,
                        "eta_min": float(chosen_route.eta_min),
                    },
                )

                edge_props = {
                    "assigned_at": int(time.time()),
                    "computed_at": int(time.time()),
                    "eta_min": float(chosen_route.eta_min),
                    "fastest_eta_min": float(chosen_route.fastest_eta_min),
                    "extra_minutes": float(chosen_route.extra_minutes),
                    "distance_km": float(chosen_route.distance_km),
                    "encoded_geometry": str(chosen_route.encoded_geometry),
                    "routing_source": str(chosen_route.routing_source),
                    "uses_hazard_road": bool(chosen_route.uses_hazard_road),
                    "no_safe_route": bool(chosen_route.no_safe_route),
                    "status": "proposed",
                    "reason": str(assignment.route_description or chosen_route.reason),
                }
                self.conn.upsertEdge(
                    "Resource",
                    assignment.resource_id,
                    "assigned_to",
                    "Person",
                    assignment.top_victim_id,
                    edge_props,
                )
                self.send_alert(assignment)

    def _resource_zone(self, resource: dict[str, Any], fallback_zone: str) -> str:
        res_id = str(resource.get("id", ""))
        if res_id:
            edges = self.conn.getEdges("Resource", res_id, "serves")
            if isinstance(edges, list) and edges:
                first = edges[0]
                if isinstance(first, dict):
                    to_id = first.get("to_id")
                    if to_id:
                        return str(to_id)

        lat = resource.get("location_lat")
        lng = resource.get("location_lng")
        if lat is not None and lng is not None:
            try:
                zones = self.conn.getVertices("Zone", limit=10000)
                zone_by_dist: tuple[float, str] | None = None
                from disastergraph.graph.utils import haversine_km

                for row in zones:
                    if not isinstance(row, dict):
                        continue
                    attrs = row.get("attributes", {})
                    if not isinstance(attrs, dict):
                        continue
                    zid = str(row.get("v_id", ""))
                    zlat = attrs.get("centroid_lat")
                    zlng = attrs.get("centroid_lng")
                    if not zid or zlat is None or zlng is None:
                        continue
                    dist = haversine_km(float(lat), float(lng), float(zlat), float(zlng))
                    if zone_by_dist is None or dist < zone_by_dist[0]:
                        zone_by_dist = (dist, zid)
                if zone_by_dist:
                    return zone_by_dist[1]
            except Exception as exc:
                LOGGER.debug(
                    "Failed to infer resource zone from coordinates for %s: %s",
                    res_id or "unknown-resource",
                    exc,
                )

        return fallback_zone

    def _normalize_vertex_rows(
        self, response: list[dict[str, Any]] | dict[str, Any], expected_key: str
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]]
        if isinstance(response, list):
            blocks = response
        else:
            blocks = [response]

        rows: list[dict[str, Any]] = []
        for block in blocks:
            for key in (expected_key, "result", "vertices", "output"):
                maybe_rows = block.get(key)
                if isinstance(maybe_rows, list):
                    for item in maybe_rows:
                        if not isinstance(item, dict):
                            continue
                        attrs = item.get("attributes", {}) if "attributes" in item else item
                        v_id = item.get("v_id") or attrs.get("id") or attrs.get("zone_id")
                        if isinstance(attrs, dict):
                            flat = dict(attrs)
                            if v_id:
                                flat.setdefault("id", str(v_id))
                            rows.append(flat)
                    if rows:
                        return rows
        return rows

    def _normalize_route(
        self,
        response: list[dict[str, Any]] | dict[str, Any],
        from_zone: str,
        to_zone: str,
    ) -> dict[str, Any]:
        path = [from_zone, to_zone]
        total_time = 0.0

        blocks = response if isinstance(response, list) else [response]
        for block in blocks:
            reversed_path = block.get("path_reversed") if isinstance(block, dict) else None
            if isinstance(reversed_path, list) and reversed_path:
                try:
                    path = list(reversed(reversed_path))
                except TypeError:
                    path = [from_zone, to_zone]
            result_rows = block.get("result")
            if isinstance(result_rows, list) and result_rows:
                first = result_rows[0]
                attrs = first.get("attributes", {}) if isinstance(first, dict) else {}
                best_time = attrs.get("best_time", attrs.get("@best_time", total_time))
                total_time = float(best_time or total_time)
        return {"path": path, "total_time": total_time}

    def generate_assignment(
        self,
        zone: dict[str, Any],
        victims: list[dict[str, Any]],
        resources: list[dict[str, Any]],
        route: dict[str, Any] | None = None,
        candidates: list[dict[str, Any]] | None = None,
    ) -> Assignment:
        prompt = build_assignment_prompt(zone, victims, resources, route, candidates=candidates)

        text = self.llm.complete_json(prompt).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise
            payload = json.loads(text[start : end + 1])

        chosen_id = str(payload.get("resource_id", "")).strip()
        chosen_cand = None

        if candidates:
            valid_ids = [str(c.get("resource_id", "")).strip() for c in candidates if c.get("resource_id")]
            if chosen_id not in valid_ids:
                LOGGER.warning(
                    "Agent selected invalid/hallucinated resource_id '%s' (not in allowed %s); rejecting and enforcing top deterministic candidate '%s'",
                    chosen_id,
                    valid_ids,
                    valid_ids[0] if valid_ids else "none",
                )
                chosen_id = valid_ids[0] if valid_ids else chosen_id

            chosen_cand = next((c for c in candidates if str(c.get("resource_id", "")).strip() == chosen_id), candidates[0])

        real_eta = (
            float(chosen_cand.get("eta_min", 0.0))
            if chosen_cand
            else float(payload.get("eta") or (route.get("total_time", 0.0) if route else 15.0))
        )

        return Assignment(
            resource_id=chosen_id,
            resource_type=str(payload.get("resource_type", "ambulance")),
            destination_zone=str(payload.get("destination_zone", zone.get("id", ""))),
            top_victim_id=str(
                payload.get("top_victim_id")
                or victims[0].get("id", "")
                if victims
                else ""
            ),
            priority_victim_count=int(payload.get("priority_victim_count", 1)),
            route_description=str(payload.get("route_description", "Route generated via road network.")),
            action=str(payload.get("action", "Proceed with dispatch.")),
            alert_message=str(payload.get("alert_message", "Dispatch initiated.")),
            eta=real_eta,
        )

    def send_alert(self, assignment: Assignment) -> None:
        if self.bot is None:
            LOGGER.warning("Telegram bot not configured; skipping alert delivery.")
            return

        self.officer_chat_ids = self._load_officer_chat_ids()
        chat_id = self.officer_chat_ids.get(assignment.destination_zone)
        if not chat_id:
            LOGGER.warning("No officer chat mapping for zone %s", assignment.destination_zone)
            return

        message = (
            "DISASTER RESPONSE DISPATCH\n\n"
            f"{assignment.alert_message}\n\n"
            f"Route: {assignment.route_description}\n"
            f"Action: {assignment.action}\n"
            f"ETA: {assignment.eta} min"
        )
        try:
            self._run_async(self.bot.send_message(chat_id=chat_id, text=message))
        except Exception as exc:
            LOGGER.warning("Failed to send Telegram alert: %s", exc)

    def _run_async(self, coroutine: Any) -> Any:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        return loop.create_task(coroutine)


def run_agent_loop() -> None:
    agent = DisasterGraphAgent()
    while True:
        try:
            agent.run_detection_cycle()
        except Exception as exc:  # pragma: no cover
            LOGGER.exception("Detection cycle failed: %s", exc)
        time.sleep(agent.settings.agent_loop_seconds)


if __name__ == "__main__":
    run_agent_loop()
