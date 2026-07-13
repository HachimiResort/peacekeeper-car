from datetime import datetime, timezone
from types import SimpleNamespace

from mission_api.views import event_view, mission_view


def test_mission_view_does_not_require_event_key():
    value = SimpleNamespace(
        id="mission-1",
        mission_type="navigation_goal",
        robot_id="car_1",
        state="succeeded",
        request_payload={"x": 1.0},
        result_payload={"ok": True},
        error=None,
        created_at=None,
        started_at=None,
        finished_at=None,
    )

    result = mission_view(value)

    assert result["id"] == "mission-1"
    assert "event_key" not in result


def test_event_view_includes_event_key():
    occurred_at = datetime(2026, 7, 13, tzinfo=timezone.utc)
    value = SimpleNamespace(
        id="event-1",
        event_key="hazard-event-1",
        robot_id="car_1",
        mission_id=None,
        event_type="hazard_detected",
        severity="critical",
        payload={"distance_m": 1.0},
        occurred_at=occurred_at,
        received_at=None,
    )

    result = event_view(value)

    assert result["event_key"] == "hazard-event-1"
    assert result["occurred_at"] == occurred_at.isoformat()
