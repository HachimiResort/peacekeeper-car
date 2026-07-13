import subprocess
import hashlib
import io
import json
import math
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

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
from fleet_agent.audio import AudioService
from fleet_agent.config import AgentConfig, PatrolConfig, ProcessConfig, SafetyConfig, VisionConfig, _to_dict, load_config
from fleet_agent.mapping import MappingService
from fleet_agent.map_bundle import install_map_bundle
from fleet_agent import map_bundle as map_bundle_module
from fleet_agent.navigation import NavigationService
from fleet_agent.patrol import PatrolService
from fleet_agent.range_estimation import DepthFrame, ScanFrame, TargetRangeEstimator
from fleet_agent.rosmaster_control import RosmasterController
from fleet_agent.ros_control import CmdVelPublisher, DirectCmdVelSubscriber, DirectOdomPublisher, LiveMapSubscriber
from fleet_agent.state import Mode, RuntimeState
from fleet_agent.video import VideoService
from fleet_agent.vision import VisionCaptureService, VisionService, VisionUnavailable, VisionWorkerClient
from fleet_agent.vision_worker import create_server

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
        self.dynamic_processes = {}
        self.dynamic_starts = []

    def start_auto_processes(self):
        return None

    def start(self, key):
        self.started.append(key)
        return {"name": key, "status": "running"}

    def start_dynamic(self, key, name, command):
        self.dynamic_processes[key] = {"name": name, "command": command}
        self.dynamic_starts.append((key, name, command))
        self.started.append(key)
        return {"name": name, "status": "running"}

    def stop(self, key, timeout_s=4.0):
        del timeout_s
        self.stopped.append(key)
        return {"name": key, "status": "stopped"}

    def stop_all(self):
        self.stopped.append("all")
        return self.status()

    def status(self):
        statuses = {
            key: {"name": value.name, "status": "running" if key in self.started else "stopped"}
            for key, value in self.config.processes.items()
        }
        statuses.update(
            {
                key: {"name": value["name"], "status": "running" if key in self.started else "stopped"}
                for key, value in self.dynamic_processes.items()
            }
        )
        return statuses

    def topic_active(self, topic):
        del topic
        return False

    def wait_for_topic(self, topic, timeout_s=10.0, poll_s=0.5):
        del timeout_s, poll_s
        return self.topic_active(topic)


class FakeMotionController:
    def __init__(self):
        self.published = []
        self.light_commands = []
        self.buzzer_commands = []
        self.stop_count = 0
        self.shutdown_count = 0

    def publish(self, linear_x, linear_y, angular_z, ttl_ms=None):
        self.published.append((linear_x, linear_y, angular_z, ttl_ms))

    def stop(self):
        self.stop_count += 1

    def control_lights(self, left, right, duration_ms):
        command = {"left": left, "right": right, "duration_ms": duration_ms}
        self.light_commands.append(command)
        return command

    def control_buzzer(self, enabled, duration_ms):
        command = {"enabled": enabled, "duration_ms": duration_ms, "remaining_ms": duration_ms}
        self.buzzer_commands.append(command)
        return command

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


class FakeLiveMapService:
    def __init__(self):
        self.start_count = 0
        self.shutdown_count = 0

    def start(self):
        self.start_count += 1

    def shutdown(self):
        self.shutdown_count += 1

    def status(self):
        return {"available": True, "ready": self.start_count > self.shutdown_count, "has_map": False}


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

    def cancel_goal(self, wait_timeout_s=0.0):
        del wait_timeout_s
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


class FakeFrame:
    shape = (480, 640, 3)


class FakeBox:
    def __init__(self, class_id, confidence, xyxy):
        self.cls = [class_id]
        self.conf = [confidence]
        self.xyxy = [self]
        self._xyxy = xyxy

    def tolist(self):
        return self._xyxy


class FakeVisionResult:
    names = {0: "person", 15: "cat"}
    boxes = [
        FakeBox(15, 0.91234, [100.0, 50.0, 300.0, 250.0]),
        FakeBox(0, 0.80001, [10.0, 20.0, 40.0, 70.0]),
    ]
    speed = {"inference": 45.429}

    def plot(self):
        return "annotated-frame"


class FakeVisionModel:
    def __init__(self):
        self.calls = []

    def __call__(self, frame, **kwargs):
        self.calls.append((frame, kwargs))
        return [FakeVisionResult()]


class FakeVisionVideo:
    def __init__(self, frame=None):
        self.frame = frame
        self.read_count = 0

    def read_frame(self):
        self.read_count += 1
        return self.frame

    @staticmethod
    def encode_jpeg(frame):
        if frame == "annotated-frame":
            return b"annotated-jpeg"
        if isinstance(frame, FakeFrame):
            return b"source-jpeg"
        return None


class FakeVisionWorker:
    def __init__(self):
        self.requests = []

    def infer(self, jpeg):
        self.requests.append(jpeg)
        return (
            {
                "ok": True,
                "model": "yolov8n.engine",
                "detections": [],
                "label_counts": {},
                "triggers": {},
                "captured_at": 1.0,
                "annotated_image_available": True,
            },
            b"worker-jpeg",
        )

    def status(self):
        return {
            "enabled": True,
            "engine_path": "/models/yolov8n.engine",
            "model_loaded": True,
            "target_labels": ["cat"],
            "last_result": None,
            "last_error": None,
        }


class FakeRangeEstimator:
    def __init__(self):
        self.payloads = []

    def enrich(self, payload):
        self.payloads.append(payload)
        enriched = dict(payload)
        enriched["detections"] = [
            {
                **item,
                "bearing_deg": 0.0,
                "range_m": 1.234,
                "range_source": "lidar",
            }
            for item in payload.get("detections", [])
        ]
        enriched["range_measurements"] = {
            "measured_count": len(enriched["detections"]),
            "total_count": len(enriched["detections"]),
            "sources": ["lidar"],
        }
        return enriched

    def status(self):
        return {"ready": True, "scan_available": True, "depth_available": False}


def tracking_config():
    config = AgentConfig()
    config.data_dir = tempfile.mkdtemp(prefix="peacekeeper-fleet-agent-test-")
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

    def test_vision_result_is_generic_with_configurable_cat_trigger(self):
        model = FakeVisionModel()
        config = VisionConfig(
            enabled=True,
            engine_path="/models/yolov8n.engine",
            confidence=0.4,
            target_labels=["cat", "dog"],
        )
        service = VisionService(config, model_factory=lambda path: model)

        result, annotated = service.analyze(FakeFrame())

        self.assertEqual(annotated, "annotated-frame")
        self.assertEqual(model.calls[0][1]["device"], 0)
        self.assertEqual(model.calls[0][1]["imgsz"], 640)
        payload = result.as_dict()
        self.assertEqual(payload["model"], "yolov8n.engine")
        self.assertEqual(payload["image_width"], 640)
        self.assertEqual(payload["label_counts"], {"cat": 1, "person": 1})
        self.assertEqual(payload["triggers"]["cat"], {"detected": True, "count": 1})
        self.assertEqual(payload["triggers"]["dog"], {"detected": False, "count": 0})
        self.assertEqual(payload["detections"][0]["bbox"], {"x": 100.0, "y": 50.0, "width": 200.0, "height": 200.0})

    def test_vision_does_not_load_a_model_until_enabled(self):
        service = VisionService(VisionConfig(enabled=False), model_factory=lambda path: self.fail(path))

        with self.assertRaisesRegex(VisionUnavailable, "disabled"):
            service.analyze(FakeFrame())
        self.assertFalse(service.status()["model_loaded"])

    def test_vision_can_preload_the_model_before_inference(self):
        model = FakeVisionModel()
        loaded_paths = []
        service = VisionService(
            VisionConfig(enabled=True, engine_path="/models/yolov8n.engine"),
            model_factory=lambda path: loaded_paths.append(path) or model,
        )

        service.load()
        service.load()

        self.assertTrue(service.status()["model_loaded"])
        self.assertEqual(loaded_paths, ["/models/yolov8n.engine"])

    def test_vision_capture_reuses_the_agent_camera_frame_and_caches_result(self):
        video = FakeVisionVideo(FakeFrame())
        model = FakeVisionModel()
        vision = VisionService(
            VisionConfig(enabled=True, engine_path="/models/yolov8n.engine", target_labels=["cat"]),
            model_factory=lambda path: model,
        )
        service = VisionCaptureService(video, vision)

        payload = service.capture()

        self.assertEqual(video.read_count, 1)
        self.assertTrue(payload["annotated_image_available"])
        self.assertEqual(payload["triggers"]["cat"], {"detected": True, "count": 1})
        self.assertEqual(service.latest(), payload)
        self.assertEqual(service.latest_jpeg(), b"annotated-jpeg")
        self.assertTrue(service.status()["latest_available"])

    def test_vision_capture_can_attach_range_estimates_to_detections(self):
        video = FakeVisionVideo(FakeFrame())
        model = FakeVisionModel()
        range_estimator = FakeRangeEstimator()
        service = VisionCaptureService(
            video,
            VisionService(VisionConfig(enabled=True, engine_path="/models/yolov8n.engine", target_labels=["cat"]), model_factory=lambda path: model),
            range_estimator=range_estimator,
        )

        payload = service.capture()

        self.assertEqual(payload["detections"][0]["range_source"], "lidar")
        self.assertAlmostEqual(payload["detections"][0]["range_m"], 1.234)
        self.assertEqual(payload["range_measurements"], {"measured_count": 2, "total_count": 2, "sources": ["lidar"]})
        self.assertTrue(service.status()["range_estimation"]["ready"])

    def test_vision_capture_reports_unavailable_camera_without_loading_a_model(self):
        service = VisionCaptureService(
            FakeVisionVideo(),
            VisionService(VisionConfig(enabled=True, engine_path="/models/yolov8n.engine"), model_factory=lambda path: self.fail(path)),
        )

        with self.assertRaisesRegex(VisionUnavailable, "Camera frame"):
            service.capture()

    def test_vision_capture_can_delegate_to_loopback_worker_without_loading_a_model(self):
        worker = FakeVisionWorker()
        service = VisionCaptureService(
            FakeVisionVideo(FakeFrame()),
            VisionService(VisionConfig(enabled=True, engine_path="/models/yolov8n.engine"), model_factory=lambda path: self.fail(path)),
            worker=worker,
        )

        payload = service.capture()

        self.assertEqual(worker.requests, [b"source-jpeg"])
        self.assertEqual(payload["model"], "yolov8n.engine")
        self.assertEqual(service.latest_jpeg(), b"worker-jpeg")

    def test_vision_capture_can_stream_mjpeg_frames(self):
        service = VisionCaptureService(
            FakeVisionVideo(FakeFrame()),
            VisionService(VisionConfig(enabled=True, engine_path="/models/yolov8n.engine"), model_factory=lambda path: FakeVisionModel()),
        )

        frame = next(service.mjpeg_frames(0.03))

        self.assertTrue(frame.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"))
        self.assertIn(b"annotated-jpeg", frame)

    def test_range_estimator_prefers_depth_camera_and_falls_back_to_lidar(self):
        estimator = TargetRangeEstimator("/scan")
        estimator._depth = DepthFrame(
            width=640,
            height=480,
            encoding="16UC1",
            step=1280,
            is_bigendian=False,
            data=bytearray(1280 * 480),
            received_at=time.time(),
        )
        depth_data = bytearray(estimator._depth.data)
        for px, py in ((200, 150), (197, 147), (197, 153), (203, 147), (203, 153)):
            offset = py * 1280 + px * 2
            depth_data[offset:offset + 2] = int(1500).to_bytes(2, "little", signed=False)
        estimator._depth = DepthFrame(
            width=640,
            height=480,
            encoding="16UC1",
            step=1280,
            is_bigendian=False,
            data=bytes(depth_data),
            received_at=time.time(),
        )
        estimator._scan = ScanFrame(
            angle_min=-1.57,
            angle_increment=0.01,
            range_min=0.05,
            range_max=8.0,
            ranges=tuple(2.5 for _ in range(400)),
            received_at=time.time(),
        )

        payload = estimator.enrich(
            {
                "ok": True,
                "model": "yolo.engine",
                "image_width": 640,
                "image_height": 480,
                "inference_ms": 10.0,
                "captured_at": time.time(),
                "annotated_image_available": True,
                "detections": [
                    {
                        "label": "cat",
                        "confidence": 0.9,
                        "bbox": {"x": 100.0, "y": 50.0, "width": 200.0, "height": 200.0},
                    }
                ],
            }
        )
        self.assertEqual(payload["detections"][0]["range_source"], "depth_camera")
        self.assertAlmostEqual(payload["detections"][0]["range_m"], 1.5, places=3)
        self.assertEqual(payload["detections"][0]["range_quality"], "calibrated")

        estimator._depth = None
        payload = estimator.enrich(
            {
                "ok": True,
                "model": "yolo.engine",
                "image_width": 640,
                "image_height": 480,
                "inference_ms": 10.0,
                "captured_at": time.time(),
                "annotated_image_available": True,
                "detections": [
                    {
                        "label": "cat",
                        "confidence": 0.9,
                        "bbox": {"x": 288.0, "y": 100.0, "width": 64.0, "height": 120.0},
                    }
                ],
            }
        )
        self.assertEqual(payload["detections"][0]["range_source"], "lidar")
        self.assertAlmostEqual(payload["detections"][0]["range_m"], 2.5, places=3)

    def test_range_estimator_can_fallback_to_known_lidar_mount_offsets(self):
        estimator = TargetRangeEstimator("/scan")
        angle_min = -3.14
        angle_increment = 0.01
        ranges = [float("inf")] * 800
        expected_range = 0.72
        # This detection sits on the left side of the image. For the current car
        # layout the camera is approximately 90 degrees away from the lidar scan
        # forward axis, so the valid scan window appears only after the fallback
        # offset search is applied.
        bearing_rad = math.radians(27.2)
        target_angle = bearing_rad + math.radians(-90.0)
        target_index = int(round((target_angle - angle_min) / angle_increment))
        for index in range(target_index - 6, target_index + 7):
            ranges[index] = expected_range
        estimator._scan = ScanFrame(
            angle_min=angle_min,
            angle_increment=angle_increment,
            range_min=0.05,
            range_max=8.0,
            ranges=tuple(ranges),
            received_at=time.time(),
        )

        payload = estimator.enrich(
            {
                "ok": True,
                "model": "yolo.engine",
                "image_width": 640,
                "image_height": 480,
                "inference_ms": 10.0,
                "captured_at": time.time(),
                "annotated_image_available": True,
                "detections": [
                    {
                        "label": "person",
                        "confidence": 0.9,
                        "bbox": {"x": 0.0, "y": 100.0, "width": 60.0, "height": 140.0},
                    }
                ],
            }
        )

        self.assertEqual(payload["detections"][0]["range_source"], "lidar")
        self.assertAlmostEqual(payload["detections"][0]["range_m"], expected_range, places=3)
        self.assertEqual(payload["detections"][0]["range_quality"], "fallback")

    def test_vision_config_is_present_in_serialized_agent_config(self):
        config = AgentConfig(vision=VisionConfig(enabled=True, target_labels=["cat", "person"]))

        self.assertEqual(_to_dict(config)["vision"], {"enabled": True, "engine_path": "", "confidence": 0.4, "imgsz": 640, "target_labels": ["cat", "person"], "worker_url": "", "worker_timeout_s": 5.0, "monitor_fps": 2.0, "stream_fps": 5.0})

    def test_vision_worker_rejects_non_loopback_bind_addresses(self):
        config = VisionConfig(enabled=True, engine_path="/models/yolov8n.engine")

        with self.assertRaisesRegex(ValueError, "loopback"):
            create_server(config, host="0.0.0.0")

    def test_vision_worker_client_rejects_non_loopback_urls(self):
        config = VisionConfig(enabled=True, engine_path="/models/yolov8n.engine", worker_url="http://10.0.0.2:8092")

        with self.assertRaisesRegex(ValueError, "loopback"):
            VisionWorkerClient(config)

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

    def test_saved_maps_only_lists_complete_yaml_pgm_pairs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            maps_dir = Path(temp_dir) / "maps"
            maps_dir.mkdir()
            (maps_dir / "complete.yaml").write_text("image: complete.pgm\n", encoding="utf-8")
            (maps_dir / "complete.pgm").write_bytes(b"P5\n1 1\n255\n\x00")
            (maps_dir / "partial.yaml").write_text("image: partial.pgm\n", encoding="utf-8")
            service = MappingService(AgentConfig(data_dir=temp_dir), FakeProcessManager())

            records = service.saved_maps()

            self.assertEqual([item["name"] for item in records], ["complete"])
            self.assertGreater(records[0]["size_bytes"], 0)

    def test_binary_pgm_keeps_whitespace_valued_first_pixel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "map.pgm"
            path.write_bytes(b"P5\n2 1\n255\n" + bytes([10, 200]))

            width, height, pixels = MappingService._read_pgm(path)

            self.assertEqual((width, height), (2, 1))
            self.assertEqual(pixels, bytes([10, 200]))

    @unittest.skipIf(map_bundle_module.yaml is None, "PyYAML is not installed")
    def test_map_bundle_install_is_versioned_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            maps_dir = Path(temp_dir)
            yaml_bytes = b"image: map.pgm\nresolution: 0.05\norigin: [0, 0, 0]\n"
            image_bytes = b"P5\n2 1\n255\n" + bytes([10, 200])
            yaml_hash = hashlib.sha256(yaml_bytes).hexdigest()
            image_hash = hashlib.sha256(image_bytes).hexdigest()
            bundle_hash = hashlib.sha256(f"{yaml_hash}:{image_hash}".encode("ascii")).hexdigest()
            manifest = {
                "schema_version": 1,
                "logical_name": "lab",
                "version": 2,
                "yaml_file": "map.yaml",
                "image_file": "map.pgm",
                "yaml_sha256": yaml_hash,
                "image_sha256": image_hash,
                "bundle_sha256": bundle_hash,
            }
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                archive.writestr("map.yaml", yaml_bytes)
                archive.writestr("map.pgm", image_bytes)

            first = install_map_bundle(output.getvalue(), maps_dir, 1024 * 1024)
            second = install_map_bundle(output.getvalue(), maps_dir, 1024 * 1024)

            self.assertEqual(first["name"], "lab__v2")
            self.assertFalse(first["idempotent"])
            self.assertTrue(second["idempotent"])
            self.assertTrue((maps_dir / "lab__v2.yaml").exists())

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

    def test_navigation_cancel_goal_forces_local_cleanup_when_cancel_stalls(self):
        class FakeExecutor:
            def __init__(self):
                self.shutdown_called = False

            def shutdown(self):
                self.shutdown_called = True

        class FakeNode:
            def __init__(self):
                self.destroy_called = False

            def destroy_node(self):
                self.destroy_called = True

        class FakeThread:
            def __init__(self):
                self.join_called = False

            def is_alive(self):
                return True

            def join(self, timeout=None):
                del timeout
                self.join_called = True

        config = AgentConfig()
        service = NavigationService(config, FakeManagedProcessManager(config), ("", ""))
        fake_executor = FakeExecutor()
        fake_node = FakeNode()
        fake_thread = FakeThread()
        service.executor = fake_executor
        service.node = fake_node
        service.spin_thread = fake_thread
        service.action_client = object()
        service.current_goal = {"id": "nav-4"}
        with service.condition:
            service._active_goal_id = "nav-4"
            service.action_state = "canceling"
            service._cancel_requested = True
            service._cancel_sent = True
            service._cancel_requested_at = time.monotonic() - 10.0

        result = service.cancel_goal(wait_timeout_s=0.01)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action_state"], "canceled")
        self.assertIsNone(service._active_goal_id)
        self.assertEqual(service.last_result["goal_id"], "nav-4")
        self.assertEqual(service.last_result["state"], "canceled")
        self.assertTrue(fake_executor.shutdown_called)
        self.assertTrue(fake_node.destroy_called)
        self.assertTrue(fake_thread.join_called)

    def test_navigation_send_goal_recovers_from_stale_canceling_goal(self):
        class FakeExecutor:
            def __init__(self):
                self.shutdown_called = False

            def shutdown(self):
                self.shutdown_called = True

        class FakeOldNode:
            def __init__(self):
                self.destroy_called = False

            def destroy_node(self):
                self.destroy_called = True

        class FakeThread:
            def __init__(self):
                self.join_called = False

            def is_alive(self):
                return True

            def join(self, timeout=None):
                del timeout
                self.join_called = True

        class FakeNow:
            def to_msg(self):
                return {"stamp": "now"}

        class FakeClock:
            def now(self):
                return FakeNow()

        class FakeNewNode:
            def get_clock(self):
                return FakeClock()

        class FakeHeader:
            def __init__(self):
                self.frame_id = None
                self.stamp = None

        class FakePosition:
            def __init__(self):
                self.x = 0.0
                self.y = 0.0
                self.z = 0.0

        class FakeOrientation:
            def __init__(self):
                self.x = 0.0
                self.y = 0.0
                self.z = 0.0
                self.w = 1.0

        class FakePose:
            def __init__(self):
                self.position = FakePosition()
                self.orientation = FakeOrientation()

        class FakePoseStamped:
            def __init__(self):
                self.header = FakeHeader()
                self.pose = FakePose()

        class FakeNavigateToPose:
            class Goal:
                def __init__(self):
                    self.pose = FakePoseStamped()

        class FakeSendFuture:
            def __init__(self):
                self.callbacks = []

            def add_done_callback(self, callback):
                self.callbacks.append(callback)

        class FakeActionClient:
            def __init__(self, node, action_type, topic):
                self.node = node
                self.action_type = action_type
                self.topic = topic
                self.last_goal = None

            def wait_for_server(self, timeout_sec=0.0):
                del timeout_sec
                return True

            def send_goal_async(self, goal_msg, feedback_callback=None):
                del feedback_callback
                self.last_goal = goal_msg
                return FakeSendFuture()

        config = AgentConfig()
        service = NavigationService(config, FakeManagedProcessManager(config), ("", ""))
        old_executor = FakeExecutor()
        old_node = FakeOldNode()
        old_thread = FakeThread()
        service.executor = old_executor
        service.node = old_node
        service.spin_thread = old_thread
        service.action_client = object()
        service.current_goal = {"id": "nav-4", "x": 0.0, "y": 0.0, "yaw": 0.0}

        def fake_require_running():
            return None

        def fake_ensure_ros_node():
            service.node = FakeNewNode()
            service._ros_types = {
                "NavigateToPose": FakeNavigateToPose,
                "ActionClient": FakeActionClient,
            }

        service._require_running = fake_require_running
        service._ensure_ros_node = fake_ensure_ros_node

        with service.condition:
            service._active_goal_id = "nav-4"
            service.action_state = "canceling"
            service._cancel_requested = True
            service._cancel_sent = True
            service._cancel_requested_at = time.monotonic() - 10.0

        result = service.send_goal(1.0, 2.0, 0.5)

        self.assertEqual(result["goal_id"], "nav-1")
        self.assertEqual(result["action_state"], "sending")
        self.assertEqual(service.last_result["goal_id"], "nav-4")
        self.assertEqual(service.last_result["state"], "canceled")
        self.assertEqual(service._active_goal_id, "nav-1")
        self.assertTrue(old_executor.shutdown_called)
        self.assertTrue(old_node.destroy_called)
        self.assertTrue(old_thread.join_called)

    def test_navigation_start_resets_stale_ros_client_before_relaunch(self):
        class FakeExecutor:
            def __init__(self):
                self.shutdown_called = False

            def shutdown(self):
                self.shutdown_called = True

        class FakeNode:
            def __init__(self):
                self.destroy_called = False

            def destroy_node(self):
                self.destroy_called = True

        class FakeThread:
            def __init__(self):
                self.join_called = False

            def is_alive(self):
                return True

            def join(self, timeout=None):
                del timeout
                self.join_called = True

        with tempfile.TemporaryDirectory() as temp_dir:
            maps_dir = Path(temp_dir) / "maps"
            maps_dir.mkdir()
            (maps_dir / "lab.yaml").write_text("image: lab.pgm\n", encoding="utf-8")
            config = AgentConfig(data_dir=temp_dir)
            fake_pm = FakeManagedProcessManager(config)
            service = NavigationService(config, fake_pm, ("", ""))
            fake_executor = FakeExecutor()
            fake_node = FakeNode()
            fake_thread = FakeThread()
            service.executor = fake_executor
            service.node = fake_node
            service.spin_thread = fake_thread
            service.initial_pose_pub = object()
            service.action_client = object()
            service.goal_handle = object()
            service.goal_future = object()
            service._ros_types = {"ActionClient": object}

            status = service.start("lab")

            self.assertEqual(status["current_map"], "lab")
            self.assertEqual(fake_pm.dynamic_starts[0][0], "nav2")
            self.assertTrue(fake_executor.shutdown_called)
            self.assertTrue(fake_node.destroy_called)
            self.assertTrue(fake_thread.join_called)
            self.assertIsNone(service.node)
            self.assertIsNone(service.executor)
            self.assertIsNone(service.spin_thread)
            self.assertIsNone(service.action_client)
            self.assertIsNone(service.initial_pose_pub)

    def test_navigation_stop_resets_ros_client_state(self):
        class FakeExecutor:
            def __init__(self):
                self.shutdown_called = False

            def shutdown(self):
                self.shutdown_called = True

        class FakeNode:
            def __init__(self):
                self.destroy_called = False

            def destroy_node(self):
                self.destroy_called = True

        class FakeThread:
            def __init__(self):
                self.join_called = False

            def is_alive(self):
                return True

            def join(self, timeout=None):
                del timeout
                self.join_called = True

        config = AgentConfig()
        service = NavigationService(config, FakeManagedProcessManager(config), ("", ""))
        fake_executor = FakeExecutor()
        fake_node = FakeNode()
        fake_thread = FakeThread()
        service.current_map_name = "lab"
        service.executor = fake_executor
        service.node = fake_node
        service.spin_thread = fake_thread
        service.initial_pose_pub = object()
        service.action_client = object()
        service.goal_handle = object()
        service.goal_future = object()
        service._ros_types = {"ActionClient": object}

        result = service.stop()

        self.assertEqual(result["current_map"], None)
        self.assertTrue(fake_executor.shutdown_called)
        self.assertTrue(fake_node.destroy_called)
        self.assertTrue(fake_thread.join_called)
        self.assertIsNone(service.node)
        self.assertIsNone(service.executor)
        self.assertIsNone(service.spin_thread)
        self.assertIsNone(service.action_client)
        self.assertIsNone(service.initial_pose_pub)

    def test_navigation_reset_destroys_action_client_before_node(self):
        events = []

        class FakeExecutor:
            def shutdown(self):
                events.append("executor.shutdown")

        class FakePublisher:
            pass

        class FakeActionClient:
            def destroy(self):
                events.append("action_client.destroy")

        class FakeNode:
            def destroy_publisher(self, publisher):
                del publisher
                events.append("node.destroy_publisher")

            def destroy_node(self):
                events.append("node.destroy_node")

        class FakeThread:
            def is_alive(self):
                return False

        config = AgentConfig()
        service = NavigationService(config, FakeManagedProcessManager(config), ("", ""))
        service.executor = FakeExecutor()
        service.node = FakeNode()
        service.spin_thread = FakeThread()
        service.initial_pose_pub = FakePublisher()
        service.action_client = FakeActionClient()

        service._reset_ros_interfaces()

        self.assertEqual(
            events,
            [
                "executor.shutdown",
                "action_client.destroy",
                "node.destroy_publisher",
                "node.destroy_node",
            ],
        )

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

    def test_rosmaster_backend_writes_light_frames_to_its_existing_serial_port(self):
        class FakeSerial:
            def __init__(self):
                self.writes = []
                self.flush_count = 0

            def write(self, data):
                self.writes.append(bytes(data))

            def flush(self):
                self.flush_count += 1

        class FakeBot:
            def __init__(self):
                self.ser = FakeSerial()

            def set_car_run(self, state, speed):
                del state, speed

            def set_car_motion(self, x, y, z):
                del x, y, z

            def set_motor(self, a, b, c, d):
                del a, b, c, d

            def set_beep(self, value):
                del value

        fake = FakeBot()
        controller = RosmasterController(
            AgentConfig().control,
            AgentConfig().safety,
            bot_factory=lambda: fake,
        )

        result = controller.control_lights(True, False, duration_ms=0)
        controller.control_lights(True, True, duration_ms=100)

        self.assertEqual(result["frames"], ["FFFC067001010078", "FFFC06700202007A"])
        self.assertEqual(
            fake.ser.writes,
            [
                bytes.fromhex("FF FC 06 70 01 01 00 78"),
                bytes.fromhex("FF FC 06 70 02 02 00 7A"),
                bytes.fromhex("FF FC 06 70 03 00 64 DD"),
            ],
        )
        self.assertEqual(fake.ser.flush_count, 2)
        controller.shutdown()

    def test_rosmaster_buzzer_auto_silences_after_requested_duration(self):
        class FakeBot:
            def __init__(self):
                self.calls = []

            def set_car_run(self, state, speed):
                del state, speed

            def set_car_motion(self, x, y, z):
                del x, y, z

            def set_motor(self, a, b, c, d):
                del a, b, c, d

            def set_beep(self, value):
                self.calls.append(value)

        fake = FakeBot()
        controller = RosmasterController(
            AgentConfig().control,
            AgentConfig().safety,
            bot_factory=lambda: fake,
        )

        result = controller.control_buzzer(True, duration_ms=30)
        self.assertTrue(result["enabled"])
        self.assertGreater(result["remaining_ms"], 0)
        self.assertIn(1, fake.calls)

        time.sleep(0.08)
        self.assertFalse(controller.status()["buzzer"]["enabled"])
        self.assertEqual(fake.calls[-1], 0)
        controller.shutdown()

    def test_audio_service_only_plays_safe_assets_and_stops_its_process_group(self):
        class FakeStderr:
            def close(self):
                return None

        class FakeProcess:
            pid = 12345
            returncode = None
            stderr = FakeStderr()

            def poll(self):
                return self.returncode

            def wait(self, timeout):
                del timeout
                self.returncode = 0

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "song.mp3").write_bytes(b"audio")
            config = AgentConfig().audio
            config.asset_dir = temp_dir
            config.player_command = sys.executable
            service = AudioService(config, root)
            process = FakeProcess()
            with patch("fleet_agent.audio.subprocess.Popen", return_value=process) as popen, \
                    patch("fleet_agent.audio.os.killpg") as killpg:
                result = service.play("song.mp3", loop=True, volume=65)
                self.assertTrue(result["playing"])
                self.assertEqual(popen.call_args.args[0], [
                    sys.executable, "-nodisp", "-autoexit", "-loglevel", "error", "-volume", "65", "-loop", "0", str(root / "song.mp3"),
                ])
                service.stop()
                killpg.assert_called_once()

            with self.assertRaises(ValueError):
                service.play("../outside.mp3")

            installed = service.install("cue.ogg", b"new-audio")
            self.assertEqual(installed["asset"], {"name": "cue.ogg", "bytes": 9})
            self.assertEqual((root / "cue.ogg").read_bytes(), b"new-audio")
            with self.assertRaises(ValueError):
                service.install("../outside.mp3", b"audio")

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
    def test_light_api_uses_the_rosmaster_controller(self):
        config = tracking_config()
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        fake_live_map = FakeLiveMapService()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "LiveMapSubscriber", return_value=fake_live_map):
            fake_odom_cls.return_value.status.return_value = {"ready": True}
            app = app_module.create_app(config)
            with TestClient(app) as client:
                response = client.post(
                    "/api/control/lights",
                    json={"left": True, "right": False, "duration_ms": 100},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "left": True, "right": False, "duration_ms": 100})
        self.assertEqual(fake_controller.light_commands, [{"left": True, "right": False, "duration_ms": 100}])

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_buzzer_api_uses_the_rosmaster_controller(self):
        config = tracking_config()
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        fake_live_map = FakeLiveMapService()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "LiveMapSubscriber", return_value=fake_live_map):
            fake_odom_cls.return_value.status.return_value = {"ready": True}
            app = app_module.create_app(config)
            with TestClient(app) as client:
                response = client.post(
                    "/api/control/buzzer",
                    json={"enabled": True, "duration_ms": 250},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "enabled": True, "duration_ms": 250, "remaining_ms": 250})
        self.assertEqual(fake_controller.buzzer_commands, [{"enabled": True, "duration_ms": 250, "remaining_ms": 250}])

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_audio_api_lists_safe_assets_and_rejects_missing_assets(self):
        config = tracking_config()
        audio_dir = Path(config.data_dir) / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        (audio_dir / "cue.mp3").write_bytes(b"audio")
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        fake_live_map = FakeLiveMapService()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "LiveMapSubscriber", return_value=fake_live_map):
            fake_odom_cls.return_value.status.return_value = {"ready": True}
            app = app_module.create_app(config)
            with TestClient(app) as client:
                assets = client.get("/api/audio/assets")
                installed = client.post("/api/audio/install", files={"bundle": ("arrival.wav", b"new-audio", "audio/wav")})
                missing = client.post("/api/audio/play", json={"asset": "missing.mp3"})

        self.assertEqual(assets.status_code, 200)
        self.assertEqual(assets.json()["assets"], [{"name": "cue.mp3", "bytes": 5}])
        self.assertEqual(installed.status_code, 200)
        self.assertEqual(installed.json()["asset"], {"name": "arrival.wav", "bytes": 9})
        self.assertEqual(missing.status_code, 404)

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_api_requires_shared_token_when_enabled(self):
        config = tracking_config()
        config.security.require_token = True
        config.security.shared_token = "test-shared-token"
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        fake_live_map = FakeLiveMapService()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "LiveMapSubscriber", return_value=fake_live_map):
            fake_odom_cls.return_value.status.return_value = {"ready": True}
            app = app_module.create_app(config)
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/status").status_code, 401)
                self.assertEqual(
                    client.get("/api/status?token=test-shared-token").status_code,
                    401,
                )
                response = client.get(
                    "/api/status",
                    headers={"X-Peacekeeper-Token": "test-shared-token"},
                )
                self.assertEqual(response.status_code, 200)

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_required_token_must_be_configured(self):
        config = tracking_config()
        config.security.require_token = True
        config.security.shared_token = ""

        with self.assertRaisesRegex(RuntimeError, "PEACEKEEPER_SHARED_TOKEN"):
            app_module.create_app(config)

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_tracking_mode_rejects_nonzero_manual_cmd(self):
        config = tracking_config()
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        fake_live_map = FakeLiveMapService()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "LiveMapSubscriber", return_value=fake_live_map):
            fake_odom_cls.return_value.status.return_value = {"ready": True}
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
        fake_live_map = FakeLiveMapService()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "LiveMapSubscriber", return_value=fake_live_map):
            fake_odom_cls.return_value.status.return_value = {"ready": True}
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
        fake_live_map = FakeLiveMapService()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "LiveMapSubscriber", return_value=fake_live_map):
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
        fake_live_map = FakeLiveMapService()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "LiveMapSubscriber", return_value=fake_live_map):
            fake_odom_cls.return_value.status.return_value = {"ready": True}
            app = app_module.create_app(config)
            with TestClient(app) as client:
                response = client.post("/api/mapping/save", json={"name": "lab"})
                self.assertEqual(response.status_code, 409)
                self.assertIn("/map is not active", response.json()["message"])
                self.assertIn("diagnostics", response.json())

    @unittest.skipIf(API_TESTS_UNAVAILABLE, "FastAPI app test dependencies are not available")
    def test_navigation_start_missing_map_returns_404(self):
        config = tracking_config()
        fake_pm = FakeManagedProcessManager(config)
        fake_controller = FakeMotionController()
        fake_subscriber = FakeDirectSubscriber()
        fake_live_map = FakeLiveMapService()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "LiveMapSubscriber", return_value=fake_live_map), \
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
        fake_live_map = FakeLiveMapService()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "LiveMapSubscriber", return_value=fake_live_map), \
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
        fake_live_map = FakeLiveMapService()
        with patch.object(app_module, "ProcessManager", return_value=fake_pm), \
                patch.object(app_module, "DirectOdomPublisher") as fake_odom_cls, \
                patch.object(app_module, "RosmasterController", return_value=fake_controller), \
                patch.object(app_module, "DirectCmdVelSubscriber", return_value=fake_subscriber), \
                patch.object(app_module, "LiveMapSubscriber", return_value=fake_live_map), \
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

    def test_live_map_subscriber_renders_latest_occupancy_grid(self):
        class Position:
            x = -1.0
            y = 2.5

        class Origin:
            position = Position()

        class Info:
            width = 2
            height = 2
            resolution = 0.05
            origin = Origin()

        class Header:
            frame_id = "map"

        class Grid:
            info = Info()
            header = Header()
            data = [-1, 0, 50, 100]

        subscriber = LiveMapSubscriber("/map")
        subscriber._on_map(Grid())

        status = subscriber.status()
        self.assertTrue(status["has_map"])
        self.assertEqual((status["width"], status["height"]), (2, 2))
        self.assertEqual(status["origin"], [-1.0, 2.5, 0.0])
        self.assertEqual(subscriber.render_png()[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
