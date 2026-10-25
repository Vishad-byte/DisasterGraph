from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

LOGGER = logging.getLogger(__name__)


def prune_stale_events(
    conn: Any,
    ttl_hours: int = 24,
    archive_instead_of_delete: bool = False,
) -> dict[str, int]:
    """Prune or expire disaster events older than TTL hours to respect AuraDB Free limits."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    LOGGER.info("Pruning events older than %s (TTL: %d hours)", cutoff_str, ttl_hours)

    if archive_instead_of_delete:
        cypher_expire = """
        MATCH (e:DisasterEvent)
        WHERE e.status = 'active' AND (
            (e.ingested_at IS NOT NULL AND e.ingested_at < $cutoff) OR
            (e.timestamp IS NOT NULL AND e.timestamp < $cutoff)
        )
        SET e.status = 'expired'
        RETURN count(e) AS expired_count
        """
        res = conn.run_query(cypher_expire, {"cutoff": cutoff_str})
        affected_count = int(res[0]["expired_count"]) if res else 0
    else:
        # Detach delete old events
        cypher_delete = """
        MATCH (e:DisasterEvent)
        WHERE (e.ingested_at IS NOT NULL AND e.ingested_at < $cutoff) OR
              (e.timestamp IS NOT NULL AND e.timestamp < $cutoff) OR
              e.status = 'expired'
        DETACH DELETE e
        RETURN count(e) AS deleted_count
        """
        res = conn.run_query(cypher_delete, {"cutoff": cutoff_str})
        affected_count = int(res[0]["deleted_count"]) if res else 0

    # Clean up zone severity if no active events remain
    cypher_reset_zones = """
    MATCH (z:Zone {is_affected: true})
    WHERE NOT (z)<-[:AFFECTS]-(:DisasterEvent {status: 'active'})
    SET z.is_affected = false, z.disaster_severity = 0.0
    RETURN count(z) AS reset_count
    """
    reset_res = conn.run_query(cypher_reset_zones)
    reset_count = int(reset_res[0]["reset_count"]) if reset_res else 0

    return {"pruned_events": affected_count, "reset_zones": reset_count}
