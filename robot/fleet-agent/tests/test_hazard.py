import tempfile
import time
import unittest
from pathlib import Path

from fleet_agent.config import HazardConfig, VisionConfig
from fleet_agent.hazard import EvidenceStore, HazardSupervisor
from fleet_agent.state import Mode, RuntimeState


class FakePatrol:
    def __init__(self, actions):
        self.actions = actions
        self.state = "running"
        self.pause_count = 0
        self.resume_count = 0
        self.cancel_count = 0

    def status(self):
        return {"state": self.state, "route_name": "demo", "current_index": 1}

    def pause(self):
        self.actions.append("patrol.pause")
        self.pause_count += 1
        self.state = "paused"

    def resume(self):
        self.actions.append("patrol.resume")
        self.resume_count += 1
        self.state = "running"

    def cancel(self, *_args, **_kwargs):
        self.actions.append("patrol.cancel")
        self.cancel_count += 1
        self.state = "canceled"


class FakeNavigation:
    def __init__(self, actions):
        self.actions = actions
        self.cancel_count = 0

    def cancel_goal(self):
        self.actions.append("navigation.cancel")
        self.cancel_count += 1


class FakeArbiter:
    def __init__(self, actions):
        self.actions = actions
        self.stop_count = 0

    def stop(self):
        self.actions.append("arbiter.stop")
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


class FakeAudio:
    def __init__(self, actions, fail=False):
        self.actions = actions
        self.fail = fail
        self.play_count = 0
        self.stop_count = 0
        self.playing = False
        self.asset = None
        self.last_error = None

    def assets(self):
        return {"ok": True, "assets": [{"name": "cat-alert.mp3", "bytes": 5}]}

    def status(self):
        return {
            "enabled": True,
            "available": True,
            "playing": self.playing,
            "asset": self.asset,
            "loop": True,
            "volume": 100,
            "last_error": self.last_error,
        }

    def play(self, asset, loop=False, volume=100):
        self.actions.append("audio.play")
        self.play_count += 1
        if self.fail:
            self.last_error = "speaker unavailable"
            raise RuntimeError(self.last_error)
        self.playing = True
        self.asset = asset
        return {"ok": True, "playing": True, "asset": asset, "loop": loop, "volume": volume}

    def stop(self):
        self.actions.append("audio.stop")
        self.stop_count += 1
        self.playing = False
        self.asset = None
        return {"ok": True, "playing": False}


class HazardTests(unittest.TestCase):
    def build(self, clear_after_s=10.0, audio_fail=False):
        temp = tempfile.TemporaryDirectory()
        config = HazardConfig(
            robot_id="car_1",
            confirmation_hits=3,
            confirmation_window=5,
            clear_after_s=clear_after_s,
            cooldown_s=0,
            alarm_audio_asset="cat-alert.mp3",
            alarm_audio_volume=100,
            alarm_audio_loop=True,
        )
        actions = []
        state = RuntimeState(mode=Mode.NAV_PATROL)
        patrol = FakePatrol(actions)
        navigation = FakeNavigation(actions)
        arbiter = FakeArbiter(actions)
        vision = FakeVision()
        outbox = FakeOutbox()
        audio = FakeAudio(actions, fail=audio_fail)
        supervisor = HazardSupervisor(
            config, state, patrol, navigation, arbiter, vision, audio,
            EvidenceStore(Path(temp.name), 1024 * 1024), outbox,
            lambda: {"model_loaded": True, "last_error": None, "range_estimation": {"scan_available": True}},
        )
        supervisor.set_enabled(True)
        return temp, supervisor, state, patrol, navigation, arbiter, outbox, audio, actions

    @staticmethod
    def payload(quality="calibrated", detections=True, range_m=1.0):
        values = [] if not detections else [{
            "label": "cat", "confidence": 0.9, "range_m": range_m,
            "range_source": "lidar", "range_quality": quality,
            "localization_valid": False,
        }]
        return {"model": "yolo.engine", "detections": values, "captured_at": time.time()}

    def test_requires_three_confirmed_frames_before_stopping(self):
        temp, supervisor, state, patrol, navigation, arbiter, outbox, audio, actions = self.build()
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
        self.assertEqual(audio.play_count, 1)
        self.assertLess(actions.index("arbiter.stop"), actions.index("audio.play"))
        self.assertTrue(supervisor.status()["alarm_audio"]["playing"])

        supervisor.observe(self.payload())
        supervisor.observe(self.payload())
        self.assertEqual(audio.play_count, 1)

    def test_fallback_lidar_measurement_can_trigger_stop(self):
        temp, supervisor, _state, patrol, _navigation, _arbiter, _outbox, audio, _actions = self.build()
        self.addCleanup(temp.cleanup)
        for _ in range(3):
            supervisor.observe(self.payload("fallback"))
        self.assertEqual(patrol.pause_count, 1)
        self.assertEqual(audio.play_count, 1)

    def test_target_outside_stop_distance_never_triggers_alarm(self):
        temp, supervisor, _state, patrol, _navigation, _arbiter, _outbox, audio, _actions = self.build()
        self.addCleanup(temp.cleanup)
        for _ in range(5):
            supervisor.observe(self.payload(range_m=2.0))
        self.assertEqual(patrol.pause_count, 0)
        self.assertEqual(audio.play_count, 0)

    def test_clear_frames_resume_a_paused_patrol(self):
        temp, supervisor, state, patrol, _navigation, _arbiter, _outbox, audio, actions = self.build(clear_after_s=0)
        self.addCleanup(temp.cleanup)
        for _ in range(3):
            supervisor.observe(self.payload())
        supervisor.observe(self.payload(detections=False))
        self.assertEqual(patrol.resume_count, 1)
        self.assertEqual(state.mode, Mode.NAV_PATROL)
        self.assertEqual(supervisor.status()["state"], "MONITORING")
        self.assertEqual(audio.stop_count, 1)
        self.assertLess(actions.index("audio.stop"), actions.index("patrol.resume"))

    def test_audio_failure_does_not_block_stop_evidence_or_alert(self):
        temp, supervisor, state, patrol, navigation, arbiter, outbox, audio, _actions = self.build(audio_fail=True)
        self.addCleanup(temp.cleanup)
        for _ in range(3):
            supervisor.observe(self.payload())

        self.assertEqual(state.mode, Mode.HAZARD_HOLD)
        self.assertEqual(patrol.pause_count, 1)
        self.assertEqual(navigation.cancel_count, 1)
        self.assertEqual(arbiter.stop_count, 1)
        self.assertEqual(audio.play_count, 1)
        self.assertEqual(len(outbox.records), 1)
        self.assertEqual(supervisor.status()["alarm_audio"]["last_error"], "speaker unavailable")
        timeline = outbox.records[0][0]["payload"]["action_timeline"]
        self.assertEqual(timeline[-1]["action"], "hazard_alarm_failed")

    def test_hold_keeps_alarm_and_takeover_stops_it(self):
        temp, supervisor, state, _patrol, _navigation, _arbiter, _outbox, audio, _actions = self.build()
        self.addCleanup(temp.cleanup)
        for _ in range(3):
            supervisor.observe(self.payload())
        event_key = supervisor.status()["current_event_key"]

        supervisor.set_hold(event_key, True)
        self.assertTrue(audio.playing)
        self.assertEqual(audio.stop_count, 0)

        supervisor.take_over(event_key)
        self.assertEqual(state.mode, Mode.MANUAL)
        self.assertFalse(audio.playing)
        self.assertEqual(audio.stop_count, 1)


if __name__ == "__main__":
    unittest.main()
