import tempfile
import time
import unittest
from pathlib import Path

from fleet_agent.config import HazardConfig, VisionConfig
from fleet_agent.hazard import EvidenceStore, HazardSupervisor
from fleet_agent.state import Mode, RuntimeState


class FakePatrol:
    def __init__(self):
        self.state = "running"
        self.pause_count = 0
        self.resume_count = 0
        self.cancel_count = 0

    def status(self):
        return {"state": self.state, "route_name": "demo", "current_index": 1}

    def pause(self):
        self.pause_count += 1
        self.state = "paused"

    def resume(self):
        self.resume_count += 1
        self.state = "running"

    def cancel(self, *_args, **_kwargs):
        self.cancel_count += 1
        self.state = "canceled"


class FakeNavigation:
    def __init__(self):
        self.cancel_count = 0

    def cancel_goal(self):
        self.cancel_count += 1


class FakeArbiter:
    def __init__(self):
        self.stop_count = 0

    def stop(self):
        self.stop_count += 1


class FakeVision:
    def __init__(self):
        self.vision = type("Vision", (), {"config": VisionConfig(target_labels=["cat"])})()
        self.enabled = False

    def set_monitor_enabled(self, enabled):
        self.enabled = enabled

    def latest_source_jpeg(self):
        return b"raw"

    def latest_jpeg(self):
        return b"annotated"


class FakeOutbox:
    def __init__(self):
        self.records = []

    def enqueue(self, event, artifacts):
        self.records.append((event, artifacts))

    def status(self):
        return {"pending": len(self.records), "mission_api_configured": False, "last_error": None}


class HazardTests(unittest.TestCase):
    def build(self, clear_after_s=10.0):
        temp = tempfile.TemporaryDirectory()
        config = HazardConfig(robot_id="car_1", confirmation_hits=3, confirmation_window=5, clear_after_s=clear_after_s, cooldown_s=0)
        state = RuntimeState(mode=Mode.NAV_PATROL)
        patrol = FakePatrol()
        navigation = FakeNavigation()
        arbiter = FakeArbiter()
        vision = FakeVision()
        outbox = FakeOutbox()
        supervisor = HazardSupervisor(
            config, state, patrol, navigation, arbiter, vision,
            EvidenceStore(Path(temp.name), 1024 * 1024), outbox,
            lambda: {"model_loaded": True, "last_error": None, "range_estimation": {"scan_available": True}},
        )
        supervisor.set_enabled(True)
        return temp, supervisor, state, patrol, navigation, arbiter, outbox

    @staticmethod
    def payload(quality="calibrated", detections=True):
        values = [] if not detections else [{
            "label": "cat", "confidence": 0.9, "range_m": 1.0,
            "range_source": "lidar", "range_quality": quality,
            "localization_valid": False,
        }]
        return {"model": "yolo.engine", "detections": values, "captured_at": time.time()}

    def test_requires_three_confirmed_frames_before_stopping(self):
        temp, supervisor, state, patrol, navigation, arbiter, outbox = self.build()
        self.addCleanup(temp.cleanup)
        supervisor.observe(self.payload())
        supervisor.observe(self.payload())
        self.assertEqual(patrol.pause_count, 0)
        supervisor.observe(self.payload())
        self.assertEqual(patrol.pause_count, 1)
        self.assertEqual(navigation.cancel_count, 1)
        self.assertEqual(arbiter.stop_count, 1)
        self.assertEqual(state.mode, Mode.HAZARD_HOLD)
        self.assertEqual(supervisor.status()["state"], "HOLDING")
        self.assertEqual(len(outbox.records), 1)

    def test_fallback_lidar_measurement_never_triggers_stop(self):
        temp, supervisor, _state, patrol, _navigation, _arbiter, _outbox = self.build()
        self.addCleanup(temp.cleanup)
        for _ in range(5):
            supervisor.observe(self.payload("fallback"))
        self.assertEqual(patrol.pause_count, 0)

    def test_clear_frames_resume_a_paused_patrol(self):
        temp, supervisor, state, patrol, _navigation, _arbiter, _outbox = self.build(clear_after_s=0)
        self.addCleanup(temp.cleanup)
        for _ in range(3):
            supervisor.observe(self.payload())
        supervisor.observe(self.payload(detections=False))
        self.assertEqual(patrol.resume_count, 1)
        self.assertEqual(state.mode, Mode.NAV_PATROL)
        self.assertEqual(supervisor.status()["state"], "MONITORING")


if __name__ == "__main__":
    unittest.main()
