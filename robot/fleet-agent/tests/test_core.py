import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - depends on optional local test deps
    TestClient = None

try:
    import fleet_agent.app as app_module
except Exception:  # pragma: no cover - depends on optional local test deps
    app_module = None

from fleet_agent.command_arbiter import CommandArbiter
from fleet_agent.config import AgentConfig, ProcessConfig, SafetyConfig, load_config
from fleet_agent.mapping import MappingService
from fleet_agent.rosmaster_control import RosmasterController
from fleet_agent.ros_control import CmdVelPublisher, DirectCmdVelSubscriber
from fleet_agent.state import Mode, RuntimeState
from fleet_agent.video import VideoService

API_TESTS_UNAVAILABLE = TestClient is None or app_module is None


class FakeProcessManager:
    def __init__(self):
        self.commands = []

    def run_ros_command(self, command, timeout_s=10.0):
        self.commands.append(command)
        prefix = command.split("-f ", 1)[1].split(" ", 1)[0]
        path = Path(prefix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".yaml").write_text("image: test.pgm\n", encoding="utf-8")
        path.with_suffix(".pgm").write_text("P2\n1 1\n255\n0\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="saved", stderr="")


class FakeManagedProcessManager:
    def __init__(self, config):
        self.config = config
        self.started = []
        self.stopped = []

    def start_auto_processes(self):
        return None

    def start(self, key):
        self.started.append(key)
        return {"name": key, "status": "running"}

    def stop(self, key, timeout_s=4.0):
        del timeout_s
        self.stopped.append(key)
        return {"name": key, "status": "stopped"}

    def stop_all(self):
        self.stopped.append("all")
        return self.status()

    def status(self):
        return {
            key: {"name": value.name, "status": "running" if key in self.started else "stopped"}
            for key, value in self.config.processes.items()
        }

    def topic_active(self, topic):
        del topic
        return False


class FakeMotionController:
    def __init__(self):
        self.published = []
        self.stop_count = 0
        self.shutdown_count = 0

    def publish(self, linear_x, linear_y, angular_z, ttl_ms=None):
        self.published.append((linear_x, linear_y, angular_z, ttl_ms))

    def stop(self):
        self.stop_count += 1

    def shutdown(self):
        self.shutdown_count += 1

    def status(self):
        return {"available": True, "publisher_ready": True, "cmd_vel_subscribers": 0}


class FakeDirectSubscriber:
    def __init__(self):
        self.start_count = 0
        self.stop_count = 0
        self.shutdown_count = 0

    def start(self):
        self.start_count += 1

    def stop(self):
        self.stop_count += 1

    def shutdown(self):
        self.shutdown_count += 1

    def status(self):
        return {"available": True, "ready": self.start_count > self.shutdown_count}


def tracking_config():
    config = AgentConfig()
    config.processes = {
        "lidar": ProcessConfig(name="lidar", command="lidar"),
        "laser_tracker": ProcessConfig(name="laser_tracker", command="laser_tracker"),
    }
    return config


class CoreTests(unittest.TestCase):
    def test_default_config_loads_without_yaml_dependency(self):
        config = load_config()
        self.assertEqual(config.port, 8001)
        self.assertEqual(config.ros.cmd_vel_topic, "/cmd_vel")
        self.assertFalse(config.ros.mock_cmd_vel)

    def test_mock_cmd_vel_records_and_ttl_stops(self):
        safety = SafetyConfig(default_ttl_ms=80, watchdog_period_s=0.02)
        publisher = CmdVelPublisher("/cmd_vel", safety, allow_mock=True)
        publisher.publish(0.2, 0.0, 0.0, ttl_ms=80)

        self.assertTrue(publisher.status()["mock_mode"])
        self.assertEqual(publisher.last_command["linear_x"], 0.2)

        time.sleep(0.16)
        self.assertEqual(publisher.last_command, {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0})
        self.assertGreaterEqual(len(publisher.published_messages), 2)
        self.assertEqual(publisher.published_messages[-1]["linear_x"], 0.0)
        publisher.shutdown()

    def test_video_fallback_snapshot_without_camera(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = VideoService(AgentConfig().video, Path(temp_dir))
            result = video.snapshot()
            self.assertTrue(result["ok"])
            path = Path(result["path"])
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes()[:2], b"\xff\xd8")

    def test_mapping_save_sanitizes_name_and_checks_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = AgentConfig(data_dir=temp_dir)
            fake_pm = FakeProcessManager()
            service = MappingService(config, fake_pm)
            result = service.save_map("../bad name")

            self.assertTrue(result["ok"])
            self.assertEqual(result["name"], "bad_name")
            self.assertTrue(Path(result["yaml"]).exists())
            self.assertTrue(Path(result["pgm"]).exists())
            self.assertIn("bad_name", fake_pm.commands[0])

    def test_rosmaster_backend_uses_continuous_motion_by_default(self):
        class FakeBot:
            def __init__(self):
                self.calls = []

            def set_car_run(self, state, speed):
                self.calls.append(("run", state, speed))

            def set_car_motion(self, x, y, z):
                self.calls.append(("motion", x, y, z))

            def set_motor(self, a, b, c, d):
                self.calls.append(("motor", a, b, c, d))

            def set_beep(self, value):
                self.calls.append(("beep", value))

        fake = FakeBot()
        config = AgentConfig().control
        config.backend = "rosmaster"
        controller = RosmasterController(config, AgentConfig().safety, bot_factory=lambda: fake)

        controller.publish(0.2, 0.0, 0.0, ttl_ms=200)
        controller.publish(0.0, 0.2, 0.0, ttl_ms=200)
        controller.publish(0.0, 0.0, 1.0, ttl_ms=200)
        controller.stop()

        self.assertIn(("motion", 0.2, 0.0, 0.0), fake.calls)
        self.assertIn(("motion", 0.0, 0.2, 0.0), fake.calls)
        self.assertIn(("motion", 0.0, 0.0, 1.0), fake.calls)
        self.assertIn(("run", 0, 0), fake.calls)
        controller.shutdown()

    def test_rosmaster_backend_can_fall_back_to_run_state_mode(self):
        class FakeBot:
            def __init__(self):
                self.calls = []

            def set_car_run(self, state, speed):
                self.calls.append(("run", state, speed))

            def set_car_motion(self, x, y, z):
                self.calls.append(("motion", x, y, z))

            def set_motor(self, a, b, c, d):
                self.calls.append(("motor", a, b, c, d))

            def set_beep(self, value):
                self.calls.append(("beep", value))

        fake = FakeBot()
        config = AgentConfig().control
        config.backend = "rosmaster"
        config.direct_motion_mode = "run_state"
        controller = RosmasterController(config, AgentConfig().safety, bot_factory=lambda: fake)

        controller.publish(0.2, 0.0, 0.0, ttl_ms=200)
        controller.publish(0.0, 0.2, 0.0, ttl_ms=200)
        controller.publish(0.0, 0.0, 1.0, ttl_ms=200)

        self.assertIn(("run", 1, 25), fake.calls)
        self.assertIn(("run", 3, 25), fake.calls)
        self.assertIn(("run", 5, 25), fake.calls)
        controller.shutdown()

    def test_arbiter_rejects_ros_cmd_vel_until_mode_is_enabled(self):
        controller = FakeMotionController()
        state = RuntimeState()
        arbiter = CommandArbiter(controller, state, AgentConfig().safety)

        rejected = arbiter.handle_ros_command(0.1, 0.0, 0.0)
        self.assertFalse(rejected["ok"])
        self.assertEqual(controller.published, [])

        enabled = arbiter.enable_ros_cmd_vel(Mode.NAV_PATROL)
        accepted = arbiter.handle_ros_command(0.1, 0.0, 0.2)

        self.assertTrue(enabled["ok"])
        self.assertTrue(accepted["ok"])
        self.assertEqual(controller.published, [(0.1, 0.0, 0.2, 500)])

    def test_arbiter_manual_command_takes_over_nav_cmd_vel(self):
        controller = FakeMotionController()
        state = RuntimeState()
        arbiter = CommandArbiter(controller, state, AgentConfig().safety, manual_override_s=10.0)
        arbiter.enable_ros_cmd_vel(Mode.NAV_PATROL)

        manual = arbiter.handle_manual_command(0.2, 0.0, 0.0, ttl_ms=700)
        ros = arbiter.handle_ros_command(0.1, 0.0, 0.2)

        self.assertTrue(manual["ok"])
        self.assertFalse(ros["ok"])
        self.assertEqual(state.mode, Mode.MANUAL)
        self.assertEqual(controller.published, [(0.2, 0.0, 0.0, 700)])

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_tracking_mode_rejects_nonzero_manual_cmd(self):
        config = tracking_config()
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber):
            app = app_module.create_app(config)
            with TestClient(app) as client:
                self.assertEqual(client.post("/api/tracking/laser/start").status_code, 200)
                response = client.post(
                    "/api/control/manual_cmd",
                    json={"linear_x": 0.2, "linear_y": 0.0, "angular_z": 0.0, "ttl_ms": 500},
                )
                self.assertEqual(response.status_code, 409)

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_tracking_stop_stops_tracker_and_returns_idle(self):
        config = tracking_config()
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber):
            app = app_module.create_app(config)
            with TestClient(app) as client:
                self.assertEqual(client.post("/api/tracking/laser/start").status_code, 200)
                response = client.post("/api/control/stop")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["mode"], "IDLE")
                self.assertIn("laser_tracker", fake_pm.stopped)
                self.assertGreaterEqual(fake_controller.stop_count, 2)

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_direct_tracking_start_skips_chassis(self):
        config = tracking_config()
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber):
            app = app_module.create_app(config)
            with TestClient(app) as client:
                response = client.post("/api/tracking/laser/start")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(fake_pm.started, ["lidar", "laser_tracker"])
                self.assertEqual(fake_subscriber.start_count, 1)

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_direct_cmd_vel_subscriber_forwards_twist_to_controller(self):
        class Vector:
            def __init__(self, x=0.0, y=0.0, z=0.0):
                self.x = x
                self.y = y
                self.z = z

        class TwistMessage:
            def __init__(self):
                self.linear = Vector(x=0.1, y=0.2)
                self.angular = Vector(z=0.3)

        fake_controller = FakeMotionController()
        subscriber = DirectCmdVelSubscriber("/cmd_vel", fake_controller)
        subscriber.enabled = True
        subscriber.handle_twist(TwistMessage())
        self.assertEqual(fake_controller.published, [(0.1, 0.2, 0.3, None)])
        self.assertEqual(subscriber.status()["message_count"], 1)

    def test_direct_cmd_vel_subscriber_ignores_twist_when_disabled(self):
        class Vector:
            def __init__(self, x=0.0, y=0.0, z=0.0):
                self.x = x
                self.y = y
                self.z = z

        class TwistMessage:
            def __init__(self):
                self.linear = Vector(x=0.1, y=0.2)
                self.angular = Vector(z=0.3)

        fake_controller = FakeMotionController()
        subscriber = DirectCmdVelSubscriber("/cmd_vel", fake_controller)
        subscriber.handle_twist(TwistMessage())
        self.assertEqual(fake_controller.published, [])
        self.assertEqual(subscriber.status()["ignored_count"], 1)


if __name__ == "__main__":
    unittest.main()
