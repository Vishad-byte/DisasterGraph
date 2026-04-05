from __future__ import annotations

import logging
from typing import Any

from disastergraph.config import get_settings, get_tg_connection


LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class DisasterGraphTelegramBot:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.conn = get_tg_connection(self.settings)

        telegram_ext = __import__("telegram.ext", fromlist=["Application", "CommandHandler"])
        self.Application = getattr(telegram_ext, "Application")
        self.CommandHandler = getattr(telegram_ext, "CommandHandler")

    async def register(self, update: Any, context: Any) -> None:
        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text(
                "Usage: /register <officer_id> <zone_id> [name]"
            )
            return

        officer_id = str(args[0])
        zone_id = str(args[1])
        officer_name = " ".join(args[2:]).strip() or (
            update.effective_user.full_name if update.effective_user else officer_id
        )
        chat_id = str(update.effective_chat.id)

        self.conn.upsertVertex(
            "Officer",
            officer_id,
            {
                "name": officer_name,
                "telegram_chat_id": chat_id,
                "zone": zone_id,
            },
        )
        self.conn.upsertEdge("Officer", officer_id, "manages", "Zone", zone_id)

        await update.message.reply_text(
            f"Registered officer {officer_name} for zone {zone_id}."
        )

    def _zone_by_id(self, zone_id: str) -> dict[str, Any] | None:
        zones = self.conn.getVertices("Zone", limit=10000)
        for z in zones:
            if not isinstance(z, dict):
                continue
            if str(z.get("v_id", "")) == zone_id:
                attrs = z.get("attributes", {})
                if isinstance(attrs, dict):
                    attrs = dict(attrs)
                    attrs["zone_id"] = zone_id
                    return attrs
        return None

    def _zone_by_name_or_id(self, lookup: str) -> dict[str, Any] | None:
        zone = self._zone_by_id(lookup)
        if zone:
            return zone

        normalized = lookup.strip().lower()
        zones = self.conn.getVertices("Zone", limit=10000)
        for z in zones:
            if not isinstance(z, dict):
                continue
            attrs = z.get("attributes", {})
            if not isinstance(attrs, dict):
                continue
            name = str(attrs.get("name", "")).strip().lower()
            if name == normalized:
                row = dict(attrs)
                row["zone_id"] = str(z.get("v_id", ""))
                return row
        return None

    def _extract_rows(self, response: Any) -> list[dict[str, Any]]:
        blocks = response if isinstance(response, list) else [response]
        out: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for key in ("resources", "result", "vertices", "output"):
                rows = block.get(key)
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if isinstance(row, dict):
                        attrs = row.get("attributes") if "attributes" in row else row
                        if isinstance(attrs, dict):
                            out.append(attrs)
        return out

    def _available_resource_count_direct(self, zone_id: str, resource_type: str) -> int:
        count = 0
        resources = self.conn.getVertices("Resource", limit=10000)
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            attrs = resource.get("attributes", {})
            if not isinstance(attrs, dict):
                continue

            if str(attrs.get("resource_type", "")) != resource_type:
                continue
            if not bool(attrs.get("is_available", False)):
                continue

            res_id = str(resource.get("v_id", ""))
            if not res_id:
                continue

            serves = self.conn.getEdges("Resource", res_id, "serves")
            if any(isinstance(edge, dict) and str(edge.get("to_id", "")) == zone_id for edge in serves):
                count += 1
        return count

    def _available_resource_count(self, zone_id: str, resource_type: str) -> int:
        query_rows = self._extract_rows(
            self.conn.runInstalledQuery(
                "findAvailableResources",
                {
                    "zone_id": zone_id,
                    "resource_type": resource_type,
                },
            )
        )
        if query_rows:
            return len(query_rows)

        direct_count = self._available_resource_count_direct(zone_id, resource_type)
        LOGGER.info(
            "Fallback direct resource count used for zone=%s type=%s count=%s",
            zone_id,
            resource_type,
            direct_count,
        )
        return direct_count

    async def status(self, update: Any, context: Any) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /status <zone_id_or_zone_name>")
            return

        lookup = " ".join(str(x) for x in args)
        zone = self._zone_by_name_or_id(lookup)
        if not zone:
            await update.message.reply_text(f"Zone not found: {lookup}")
            return

        zone_id = str(zone.get("zone_id") or "")
        if not zone_id:
            await update.message.reply_text("Zone has no valid ID in graph.")
            return

        sev = zone.get("disaster_severity", 0)
        affected = zone.get("is_affected", False)

        ambulances = self._available_resource_count(zone_id, "ambulance")
        hospitals = self._available_resource_count(zone_id, "hospital")
        shelters = self._available_resource_count(zone_id, "shelter")

        text = (
            f"Zone: {zone.get('name', zone_id)} ({zone_id})\n"
            f"Affected: {affected}\n"
            f"Severity: {sev}\n"
            f"Available ambulances: {ambulances}\n"
            f"Available hospitals: {hospitals}\n"
            f"Available shelters: {shelters}"
        )
        await update.message.reply_text(text)

    def build_application(self) -> Any:
        app = self.Application.builder().token(self.settings.telegram_token).build()
        app.add_handler(self.CommandHandler("register", self.register))
        app.add_handler(self.CommandHandler("status", self.status))
        return app


def run_bot() -> None:
    bot = DisasterGraphTelegramBot()
    app = bot.build_application()
    app.run_polling()


if __name__ == "__main__":
    run_bot()
