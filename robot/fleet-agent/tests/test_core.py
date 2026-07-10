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

import fleet_agent.patrol as patrol_module

from fleet_agent.command_arbiter import CommandArbiter
from fleet_agent.config import AgentConfig, PatrolConfig, ProcessConfig, SafetyConfig, load_config
from fleet_agent.mapping import MappingService
from fleet_agent.navigation import NavigationService
from fleet_agent.patrol import PatrolService
from fleet_agent.rosmaster_control import RosmasterController
from fleet_agent.ros_control import CmdVelPublisher, DirectCmdVelSubscriber, DirectOdomPublisher
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


class FakeMotionSink:
    def __init__(self):
        self.commands = []
        self.stop_count = 0

    def update_command(self, linear_x, linear_y, angular_z, ttl_ms=None):
        self.commands.append((linear_x, linear_y, angular_z, ttl_ms))

    def stop(self):
        self.stop_count += 1

    def status(self):
        return {"ready": True}


class FakeNavigationService:
    last_instance = None

    def __init__(self, *args, **kwargs):
        del args, kwargs
        self.current_map_name = None
        self.cancel_count = 0
        self.stop_count = 0
        FakeNavigationService.last_instance = self

    def start(self, map_name):
        if map_name == "missing":
            raise FileNotFoundError("Map 'missing' was not found")
        self.current_map_name = map_name
        return self.status()

    def publish_initial_pose(self, x, y, yaw):
        if not self.current_map_name:
            raise RuntimeError("Nav2 is not running; call /api/navigation/start first")
        return {"ok": True, "initial_pose": {"x": x, "y": y, "yaw": yaw}}

    def send_goal(self, x, y, yaw):
        if not self.current_map_name:
            raise RuntimeError("Nav2 is not running; call /api/navigation/start first")
        return {"ok": True, "goal_id": "fake-goal", "goal": {"x": x, "y": y, "yaw": yaw}}

    def cancel_goal(self):
        self.cancel_count += 1
        return {"ok": True, "action_state": "canceled"}

    def stop(self):
        self.stop_count += 1
        self.current_map_name = None
        return self.status()

    def status(self):
        return {
            "process": None,
            "current_map": self.current_map_name,
            "current_goal": None,
            "active_goal_id": None,
            "action_state": "running" if self.current_map_name else "idle",
            "last_result": None,
            "feedback": None,
            "last_error": None,
            "ready": bool(self.current_map_name),
            "spin_thread_alive": False,
        }

    def shutdown(self):
        return None


class FakePatrolNavigation:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.goals = []
        self.results = {}
        self.cancel_count = 0

    def status(self):
        return {"ready": True, "current_map": "lab", "active_goal_id": None}

    def send_goal(self, x, y, yaw):
        goal_id = f"goal-{len(self.goals) + 1}"
        self.goals.append((x, y, yaw))
        state = self.outcomes.pop(0) if self.outcomes else "succeeded"
        self.results[goal_id] = {"goal_id": goal_id, "state": state, "error": None}
        return {"ok": True, "goal_id": goal_id}

    def wait_for_goal(self, goal_id, timeout_s=None):
        del timeout_s
        return dict(self.results[goal_id])

    def cancel_goal(self, wait_timeout_s=0.0):
        del wait_timeout_s
        self.cancel_count += 1
        return {"ok": True, "action_state": "canceled"}


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

    def test_mapping_meta_reads_yaml_pgm_and_converts_pixels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            maps_dir = Path(temp_dir) / "maps"
            maps_dir.mkdir()
            (maps_dir / "lab.yaml").write_text(
                "image: lab.pgm\nresolution: 0.05\norigin: [-10.0, -20.0, 0.0]\n",
                encoding="utf-8",
            )
            (maps_dir / "lab.pgm").write_text("P2\n2 2\n255\n0 255\n255 0\n", encoding="utf-8")
            service = MappingService(AgentConfig(data_dir=temp_dir), FakeProcessManager())

            meta = service.map_meta("lab")
            map_x, map_y = MappingService.pixel_to_map(meta, 0, 0)

            self.assertEqual(meta["width"], 2)
            self.assertEqual(meta["height"], 2)
            self.assertEqual(meta["resolution"], 0.05)
            self.assertAlmostEqual(map_x, -9.975)
            self.assertAlmostEqual(map_y, -19.925)

    def test_navigation_start_requires_existing_map(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = AgentConfig(data_dir=temp_dir)
            service = NavigationService(config, FakeManagedProcessManager(config), ("", ""))

            with self.assertRaises(FileNotFoundError):
                service.start("missing")

    def test_navigation_ignores_and_cancels_stale_goal_response(self):
        class FakeGoalHandle:
            accepted = True

            def __init__(self):
                self.cancel_count = 0

            def cancel_goal_async(self):
                self.cancel_count += 1
                return object()

        class FakeFuture:
            def __init__(self, value):
                self.value = value

            def result(self):
                return self.value

        config = AgentConfig()
        service = NavigationService(config, FakeManagedProcessManager(config), ("", ""))
        handle = FakeGoalHandle()
        with service.condition:
            service._active_goal_id = "nav-2"
            service.action_state = "sending"

        service._on_goal_response("nav-1", FakeFuture(handle))

        self.assertEqual(handle.cancel_count, 1)
        self.assertEqual(service._active_goal_id, "nav-2")
        self.assertEqual(service.action_state, "sending")

    def test_navigation_wait_returns_terminal_result(self):
        config = AgentConfig()
        service = NavigationService(config, FakeManagedProcessManager(config), ("", ""))
        with service.condition:
            service._active_goal_id = "nav-1"
            service.action_state = "active"

        service._finish_goal("nav-1", "succeeded", None, status_code=4)
        result = service.wait_for_goal("nav-1", timeout_s=0.01)

        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["status_code"], 4)

    def test_patrol_executes_points_in_order_and_converts_yaw_degrees(self):
        navigation = FakePatrolNavigation()
        stops = []
        service = PatrolService(
            PatrolConfig(routes_file="/missing", poll_period_s=0.01),
            navigation,
            before_goal=lambda: {"ok": True},
            stop_motion=lambda: stops.append(True),
            routes={
                "lab_route": {
                    "map_name": "lab",
                    "loop": False,
                    "points": [
                        {"name": "a", "x": 1, "y": 2, "yaw_deg": 90, "dwell_s": 0},
                        {"name": "b", "x": 3, "y": 4, "yaw_deg": 180, "dwell_s": 0},
                    ],
                }
            },
        )
        route = service.build_route(route_name="lab_route")

        service.start(route)
        deadline = time.monotonic() + 1.0
        while service.status()["worker_alive"] and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(service.status()["state"], "completed")
        self.assertEqual(len(navigation.goals), 2)
        self.assertAlmostEqual(navigation.goals[0][2], 1.57079632679, places=5)
        self.assertAlmostEqual(abs(navigation.goals[1][2]), 3.14159265359, places=5)
        self.assertGreaterEqual(len(stops), 1)

    def test_patrol_empty_routes_load_without_yaml_dependency(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            routes_file = Path(temp_dir) / "routes.yaml"
            routes_file.write_text("# no routes yet\nroutes: {}\n", encoding="utf-8")
            with patch.object(patrol_module, "yaml", None):
                service = PatrolService(
                    PatrolConfig(routes_file=str(routes_file)),
                    FakePatrolNavigation(),
                    before_goal=lambda: {"ok": True},
                    stop_motion=lambda: None,
                )

            self.assertEqual(service.routes_status()["routes"], {})
            self.assertIsNone(service.routes_status()["routes_error"])

    def test_patrol_stops_route_after_navigation_failure(self):
        navigation = FakePatrolNavigation(outcomes=["aborted"])
        service = PatrolService(
            PatrolConfig(routes_file="/missing", poll_period_s=0.01),
            navigation,
            before_goal=lambda: {"ok": True},
            stop_motion=lambda: None,
            routes={},
        )
        route = service.build_route(
            map_name="lab",
            points=[{"name": "bad", "x": 1, "y": 2, "yaw": 0, "dwell_s": 0}],
        )

        service.start(route)
        deadline = time.monotonic() + 1.0
        while service.status()["worker_alive"] and time.monotonic() < deadline:
            time.sleep(0.01)

        status = service.status()
        self.assertEqual(status["state"], "failed")
        self.assertIn("state=aborted", status["last_error"])

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

    def test_rosmaster_backend_exposes_motion_feedback(self):
        class FakeBot:
            def __init__(self):
                self.receive_thread_started = False

            def create_receive_threading(self):
                self.receive_thread_started = True

            def set_car_run(self, state, speed):
                del state, speed

            def set_car_motion(self, x, y, z):
                del x, y, z

            def set_motor(self, a, b, c, d):
                del a, b, c, d

            def set_beep(self, value):
                del value

            def get_motion_data(self):
                return (0.12, -0.03, 0.4)

        controller = RosmasterController(
            AgentConfig().control,
            AgentConfig().safety,
            bot_factory=FakeBot,
        )

        motion = controller.read_motion()

        self.assertEqual(motion, {"linear_x": 0.12, "linear_y": -0.03, "angular_z": 0.4})
        self.assertTrue(controller.status()["motion_feedback_available"])
        self.assertTrue(controller.status()["feedback_thread_started"])
        controller.shutdown()

    def test_direct_odom_prefers_feedback_motion_over_command_integration(self):
        class FakeFeedbackSource:
            def read_motion(self):
                return {"linear_x": 0.05, "linear_y": 0.0, "angular_z": 0.0}

        odom = DirectOdomPublisher(
            "/odom",
            "odom",
            "base_link",
            AgentConfig().safety,
            feedback_source=FakeFeedbackSource(),
        )
        odom.update_command(0.2, 0.0, 0.0, ttl_ms=500)

        with odom.lock:
            motion = odom._resolve_motion_unlocked()
            odom._integrate_pose(1.0, motion)

        self.assertEqual(odom.motion_source, "feedback")
        self.assertEqual(motion["linear_x"], 0.05)
        self.assertAlmostEqual(odom.x, 0.05, places=6)

    def test_direct_odom_falls_back_to_command_when_feedback_fails(self):
        class BrokenFeedbackSource:
            def read_motion(self):
                raise RuntimeError("feedback offline")

        odom = DirectOdomPublisher(
            "/odom",
            "odom",
            "base_link",
            AgentConfig().safety,
            feedback_source=BrokenFeedbackSource(),
        )
        odom.update_command(0.2, 0.0, 0.0, ttl_ms=500)

        with odom.lock:
            motion = odom._resolve_motion_unlocked()

        self.assertEqual(odom.motion_source, "command_fallback")
        self.assertEqual(motion["linear_x"], 0.2)
        self.assertIn("feedback offline", odom.last_error)

    def test_direct_odom_falls_back_to_command_when_feedback_stays_zero(self):
        class ZeroFeedbackSource:
            def read_motion(self):
                return {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}

        odom = DirectOdomPublisher(
            "/odom",
            "odom",
            "base_link",
            AgentConfig().safety,
            feedback_source=ZeroFeedbackSource(),
            period_s=0.05,
        )
        odom.update_command(0.2, 0.0, 0.0, ttl_ms=500)

        with odom.lock:
            odom._feedback_zero_since = time.monotonic() - 0.3
            motion = odom._resolve_motion_unlocked()

        self.assertEqual(odom.motion_source, "command_fallback_stale_feedback")
        self.assertEqual(motion["linear_x"], 0.2)
        self.assertEqual(odom.last_feedback["linear_x"], 0.0)
        self.assertIn("falling back to commanded motion", odom.last_error)

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

    def test_arbiter_notifies_motion_sink_and_stop(self):
        controller = FakeMotionController()
        sink = FakeMotionSink()
        state = RuntimeState()
        arbiter = CommandArbiter(controller, state, AgentConfig().safety, motion_sink=sink)

        arbiter.handle_manual_command(0.2, 0.1, 0.0, ttl_ms=600)
        arbiter.stop()

        self.assertEqual(sink.commands, [(0.2, 0.1, 0.0, 600)])
        self.assertEqual(sink.stop_count, 1)

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
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber):
            fake_odom_cls.return_value.status.return_value = {"ready": True}
            app = app_module.create_app(config)
            with TestClient(app) as client:
                response = client.post("/api/tracking/laser/start")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(fake_pm.started, ["lidar", "laser_tracker"])
                self.assertEqual(fake_subscriber.start_count, 1)

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_mapping_save_rejects_when_map_topic_is_inactive(self):
        config = tracking_config()
        config.processes["slam"] = ProcessConfig(name="slam", command="slam")
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber):
            fake_odom_cls.return_value.status.return_value = {"ready": True}
            app = app_module.create_app(config)
            with TestClient(app) as client:
                response = client.post("/api/mapping/save", json={"name": "lab"})
                self.assertEqual(response.status_code, 409)
                self.assertIn("/map is not active", response.json()["message"])

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_navigation_start_missing_map_returns_404(self):
        config = tracking_config()
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "NavigationService", FakeNavigationService):
            fake_odom_cls.return_value.status.return_value = {"ready": True}
            app = app_module.create_app(config)
            with TestClient(app) as client:
                response = client.post("/api/navigation/start", json={"map_name": "missing"})
                self.assertEqual(response.status_code, 404)

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_navigation_goal_rejects_when_nav2_not_started(self):
        config = tracking_config()
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "NavigationService", FakeNavigationService):
            fake_odom_cls.return_value.status.return_value = {"ready": True}
            app = app_module.create_app(config)
            with TestClient(app) as client:
                response = client.post(
                    "/api/navigation/goal",
                    json={"map_name": "lab", "x": 1.0, "y": 2.0, "yaw": 0.0},
                )
                self.assertEqual(response.status_code, 409)
                self.assertGreaterEqual(fake_controller.stop_count, 1)

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_navigation_cancel_calls_action_cancel_and_stop(self):
        config = tracking_config()
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "NavigationService", FakeNavigationService):
            fake_odom_cls.return_value.status.return_value = {"ready": True}
            app = app_module.create_app(config)
            with TestClient(app) as client:
                response = client.post("/api/navigation/cancel")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(FakeNavigationService.last_instance.cancel_count, 1)
                self.assertGreaterEqual(fake_controller.stop_count, 1)

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
