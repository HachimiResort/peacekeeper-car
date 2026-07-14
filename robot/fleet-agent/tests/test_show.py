import time
import unittest
from datetime import datetime, timedelta, timezone

from fleet_agent.show import ShowService
from fleet_agent.state import Mode, RuntimeState


class Arbiter:
    def __init__(self): self.calls = []
    def handle_show_command(self, **kwargs): self.calls.append(kwargs); return {"ok": True}
    def stop(self): self.calls.append({"stop": True})


class Controller:
    def __init__(self): self.lights = []
    def control_lights(self, left, right, duration): self.lights.append((left, right, duration))


class ShowServiceTests(unittest.TestCase):
    def test_relative_events_are_anchored_to_commit_start(self):
        state, arbiter, controller = RuntimeState(), Arbiter(), Controller()
        service = ShowService(arbiter, controller, state)
        service.prepare({"show_id": "relative", "events": [
            {"channel": "motion", "offset_ms": 0, "payload": {"linear_x": .3, "linear_y": 0, "angular_z": 0}},
            {"channel": "motion", "offset_ms": 500, "payload": {"linear_x": 0, "linear_y": 0, "angular_z": 0}},
        ]})
        start = datetime.now(timezone.utc) + timedelta(seconds=3.5)
        service.commit(start.isoformat())
        first, second = service.plan["events"]
        first_at = datetime.fromisoformat(first["execute_at_utc"])
        second_at = datetime.fromisoformat(second["execute_at_utc"])
        self.assertAlmostEqual((first_at - start).total_seconds(), 0, places=2)
        self.assertAlmostEqual((second_at - first_at).total_seconds(), .5, places=2)
        service.abort()

    def test_commit_does_not_require_three_seconds_of_extra_margin(self):
        state, arbiter, controller = RuntimeState(), Arbiter(), Controller()
        service = ShowService(arbiter, controller, state)
        service.prepare({"show_id": "short-countdown", "events": [
            {"channel": "motion", "offset_ms": 0, "payload": {"linear_x": .3, "linear_y": 0, "angular_z": 0}},
        ]})
        service.commit((datetime.now(timezone.utc) + timedelta(seconds=.75)).isoformat())
        self.assertEqual(service.status()["state"], "countdown")
        service.abort()

    def test_same_tick_motion_and_light_are_executed_and_finished_safely(self):
        state, arbiter, controller = RuntimeState(), Arbiter(), Controller()
        service = ShowService(arbiter, controller, state)
        now = datetime.now(timezone.utc).isoformat()
        service.prepare({"show_id": "s", "events": [
            {"channel": "motion", "execute_at_utc": now, "payload": {"linear_x": .1, "linear_y": 0, "angular_z": 0}},
            {"channel": "light", "execute_at_utc": now, "payload": {"effect": "both"}},
        ]})
        service.status_value["start_at_utc"] = now
        service.abort_event.clear()
        service._run(time.monotonic())
        self.assertEqual(service.status()["state"], "completed")
        self.assertTrue(any(call.get("linear_x") == .1 for call in arbiter.calls))
        self.assertIn((True, True, 0), controller.lights)
        self.assertEqual(controller.lights[-1], (False, False, 0))
