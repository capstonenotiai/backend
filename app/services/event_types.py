"""Event type adapter for deterministic planner payloads."""


def to_planner_event_type(event_type: str | None) -> str:
    return {"application": "application", "submission": "application",
            "event": "main_event", "interview": "other"}.get(event_type, "unknown")
