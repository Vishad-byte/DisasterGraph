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
from disastergraph.config import get_settings, get_tg_connection


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
        self.conn = get_tg_connection(self.settings)
        self.llm = LLMClient(self.settings)

        telegram_mod = importlib.import_module("telegram")

        self.bot = telegram_mod.Bot(token=self.settings.telegram_token)
        self.officer_chat_ids = self._load_officer_chat_ids()

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

    def run_detection_cycle(self) -> None:
        event_ids = self._extract_event_ids()
        LOGGER.info("Active events found: %s", len(event_ids))

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

                first_resource = resources[0]
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
                        "Event %s zone %s: victims=%s resources=%s route=%s",
                        event_id,
                        zone_id,
                        len(victims),
                        len(resources),
                        route,
                    )

                assignment = self.generate_assignment(zone, victims, resources, route)
                if not assignment.resource_id or not assignment.top_victim_id:
                    continue

                if self.settings.agent_verbose:
                    LOGGER.info("LLM assignment: %s", assignment)

                res_cap = int(first_resource.get("capacity", 0))
                curr_load = int(first_resource.get("current_load", 0))
                new_load = min(res_cap, curr_load + max(1, assignment.priority_victim_count))
                self.conn.runInstalledQuery(
                    "updateResourceState",
                    {
                        "res_id": assignment.resource_id,
                        "new_load": new_load,
                        "assigned_person": assignment.top_victim_id,
                        "eta_min": float(assignment.eta),
                    },
                )
                self.conn.upsertEdge(
                    "Resource",
                    assignment.resource_id,
                    "assigned_to",
                    "Person",
                    assignment.top_victim_id,
                    {
                        "assigned_at": int(time.time()),
                        "eta_min": float(assignment.eta),
                    },
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
        route: dict[str, Any],
    ) -> Assignment:
        prompt = build_assignment_prompt(zone, victims, resources, route)

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

        return Assignment(
            resource_id=str(payload.get("resource_id", "")),
            resource_type=str(payload.get("resource_type", "ambulance")),
            destination_zone=str(payload.get("destination_zone", zone.get("id", ""))),
            top_victim_id=str(
                payload.get("top_victim_id")
                or victims[0].get("id", "")
                if victims
                else ""
            ),
            priority_victim_count=int(payload.get("priority_victim_count", 1)),
            route_description=str(payload.get("route_description", "Route generated.")),
            action=str(payload.get("action", "Proceed with dispatch.")),
            alert_message=str(payload.get("alert_message", "Dispatch initiated.")),
            eta=float(payload.get("eta", route.get("total_time", 0))),
        )

    def send_alert(self, assignment: Assignment) -> None:
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
        self._run_async(self.bot.send_message(chat_id=chat_id, text=message))

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
