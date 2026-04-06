from __future__ import annotations

from disastergraph.agent.disaster_agent import DisasterGraphAgent


def run_detection_cycle_once() -> dict[str, object]:
    agent = DisasterGraphAgent()
    agent.run_detection_cycle()
    return {
        "ok": True,
        "message": "Agent detection cycle completed",
    }
