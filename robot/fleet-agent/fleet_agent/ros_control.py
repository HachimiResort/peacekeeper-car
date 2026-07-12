"""ROS2 /cmd_vel publisher/subscriber helpers with TTL safety."""
from __future__ import annotations

import math
import threading
import time
import os
import shlex
import subprocess
import sys
import binascii
import struct
import zlib
from importlib import import_module
from typing import Optional

from .config import SafetyConfig

rclpy = None
Twist = None
TransformStamped = None
Odometry = None
OccupancyGrid = None
TransformBroadcaster = None
StaticTransformBroadcaster = None
SingleThreadedExecutor = None
_ros_import_error: Optional[str] = None
_ros_environment_loaded = False


def _source_ros_environment(setup_paths) -> None:
    """Load ROS setup environment into this Python process when possible."""
    global _ros_environment_loaded
    if _ros_environment_loaded:
        return
    paths = [path for path in setup_paths if path]
    if not paths:
        return

    source_commands = [
        f'if test -f {shlex.quote(path)}; then source {shlex.quote(path)}; fi'
        for path in paths
    ]
    command = " && ".join(source_commands + ["env -0"])
    result = subprocess.run(
        ["bash", "-lc", command],
        capture_output=True,
        check=True,
    )
    for item in result.stdout.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        os.environ[key.decode()] = value.decode(errors="ignore")

    for path in reversed(os.environ.get("PYTHONPATH", "").split(os.pathsep)):
        if path and path not in sys.path:
            sys.path.insert(0, path)
    _ros_environment_loaded = True


def _ensure_ros_python(setup_paths=()) -> bool:
    """Import rclpy and geometry_msgs after optional ROS environment setup."""
    global rclpy, Twist, TransformStamped, Odometry, OccupancyGrid, TransformBroadcaster, StaticTransformBroadcaster, SingleThreadedExecutor, _ros_import_error
    if (
        rclpy is not None
        and Twist is not None
        and TransformStamped is not None
        and Odometry is not None
        and OccupancyGrid is not None
        and TransformBroadcaster is not None
        and StaticTransformBroadcaster is not None
        and SingleThreadedExecutor is not None
    ):
        return True
    try:
        _source_ros_environment(setup_paths)
        rclpy = import_module("rclpy")
        geometry_msgs = import_module("geometry_msgs.msg")
        nav_msgs = import_module("nav_msgs.msg")
        tf2_ros = import_module("tf2_ros")
        executors = import_module("rclpy.executors")
        Twist = geometry_msgs.Twist
        TransformStamped = geometry_msgs.TransformStamped
        Odometry = nav_msgs.Odometry
        OccupancyGrid = nav_msgs.OccupancyGrid
        TransformBroadcaster = tf2_ros.TransformBroadcaster
        StaticTransformBroadcaster = tf2_ros.StaticTransformBroadcaster
        SingleThreadedExecutor = executors.SingleThreadedExecutor
        _ros_import_error = None
        return True
    except Exception as exc:  # pragma: no cover - depends on ROS runtime.
        _ros_import_error = str(exc)
        rclpy = None
        Twist = None
        TransformStamped = None
        Odometry = None
        OccupancyGrid = None
        TransformBroadcaster = None
        StaticTransformBroadcaster = None
        SingleThreadedExecutor = None
        return False


class CmdVelPublisher:
    def __init__(self, topic: str, safety: SafetyConfig, allow_mock: bool = False):
        self.topic = topic
        self.safety = safety
        self.allow_mock = allow_mock
        self.mock_mode = False
        self.node = None
        self.publisher = None
        self.lock = threading.RLock()
        self.deadline: Optional[float] = None
        self.last_command = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
        self.last_publish_ok = False
        self.published_messages = []
        self._stop_event = threading.Event()
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog.start()

    @property
    def available(self) -> bool:
        return _ensure_ros_python()

    def start(self) -> None:
        if not self.available and self.allow_mock:
            with self.lock:
                self.mock_mode = True
                self.publisher = self.publisher or object()
            return
        if not self.available:
            raise RuntimeError(f"ROS2 Python modules are not available: {_ros_import_error}")
        with self.lock:
            if self.publisher:
                return
            if not rclpy.ok():
                rclpy.init(args=None)
            self.node = rclpy.create_node("peacekeeper_fleet_agent")
            self.publisher = self.node.create_publisher(Twist, self.topic, 10)

    def publish(self, linear_x: float, linear_y: float, angular_z: float, ttl_ms: Optional[int] = None) -> None:
        self.start()
        ttl = ttl_ms if ttl_ms is not None else self.safety.default_ttl_ms
        clipped = {
            "linear_x": self._clip(linear_x, self.safety.max_linear_x),
            "linear_y": self._clip(linear_y, self.safety.max_linear_y),
            "angular_z": self._clip(angular_z, self.safety.max_angular_z),
        }
        with self.lock:
            self._publish_unlocked(**clipped)
            self.last_command = clipped
            self.deadline = time.monotonic() + ttl / 1000.0

    def stop(self) -> None:
        if not self.available and not self.allow_mock:
            return
        self.start()
        with self.lock:
            self._publish_unlocked(0.0, 0.0, 0.0)
            self.last_command = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
            self.deadline = None

    def shutdown(self) -> None:
        self._stop_event.set()
        try:
            self.stop()
        except Exception:
            pass
        with self.lock:
            if self.node is not None:
                self.node.destroy_node()
            self.node = None
            self.publisher = None

    def status(self) -> dict:
        subscribers = 0
        with self.lock:
            if self.publisher is not None:
                try:
                    subscribers = int(self.publisher.get_subscription_count())
                except Exception:
                    subscribers = 0
            return {
                "available": self.available,
                "ros_import_error": _ros_import_error,
                "publisher_ready": self.publisher is not None,
                "mock_mode": self.mock_mode,
                "topic": self.topic,
                "cmd_vel_subscribers": subscribers,
                "last_publish_ok": self.last_publish_ok,
                "last_command": dict(self.last_command),
                "ttl_active": self.deadline is not None and time.monotonic() < self.deadline,
            }

    def _publish_unlocked(self, linear_x: float, linear_y: float, angular_z: float) -> None:
        if self.mock_mode:
            self.published_messages.append(
                {
                    "linear_x": float(linear_x),
                    "linear_y": float(linear_y),
                    "angular_z": float(angular_z),
                    "time": time.time(),
                }
            )
            self.last_publish_ok = True
            return
        msg = Twist()
        msg.linear.x = float(linear_x)
        msg.linear.y = float(linear_y)
        msg.angular.z = float(angular_z)
        self.publisher.publish(msg)
        self.last_publish_ok = True

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(self.safety.watchdog_period_s)
            with self.lock:
                if self.deadline is None or time.monotonic() <= self.deadline:
                    continue
                try:
                    if self.publisher is not None:
                        self._publish_unlocked(0.0, 0.0, 0.0)
                except Exception:
                    self.last_publish_ok = False
                self.last_command = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
                self.deadline = None

    @staticmethod
    def _clip(value: float, limit: float) -> float:
        return max(-limit, min(limit, float(value)))


class DirectCmdVelSubscriber:
    """Subscribe to ROS /cmd_vel and execute Twist commands through a controller.

    This is used only by the direct Rosmaster backend while running ROS-native
    behaviors such as laser tracking.
    """

    def __init__(self, topic: str, controller, setup_paths=None):
        self.topic = topic
        self.controller = controller
        self.setup_paths = tuple(setup_paths or ())
        self.node = None
        self.executor = None
        self.subscription = None
        self.lock = threading.RLock()
        self.enabled = False
        self.message_count = 0
        self.ignored_count = 0
        self.last_error: Optional[str] = None
        self._spin_thread = None

    @property
    def available(self) -> bool:
        return _ensure_ros_python(self.setup_paths)

    def start(self) -> None:
        if not self.available:
            raise RuntimeError(f"ROS2 Python modules are not available: {_ros_import_error}")
        with self.lock:
            self.enabled = True
            if self.subscription is not None:
                return
            if not rclpy.ok():
                rclpy.init(args=None)
            self.node = rclpy.create_node("peacekeeper_direct_cmd_vel_subscriber")
            self.executor = SingleThreadedExecutor()
            self.executor.add_node(self.node)
            self.subscription = self.node.create_subscription(Twist, self.topic, self._on_msg, 10)
            self._spin_thread = threading.Thread(target=self._spin, daemon=True)
            self._spin_thread.start()

    def stop(self) -> None:
        with self.lock:
            self.enabled = False

    def shutdown(self) -> None:
        with self.lock:
            self.enabled = False
            node = self.node
            executor = self.executor
            self.node = None
            self.executor = None
            self.subscription = None
        if executor is not None:
            try:
                executor.shutdown()
            except Exception:
                pass
        if node is not None:
            try:
                node.destroy_node()
            except Exception:
                pass

    def status(self) -> dict:
        with self.lock:
            return {
                "available": self.available,
                "ros_import_error": _ros_import_error,
                "ready": self.subscription is not None,
                "enabled": self.enabled,
                "topic": self.topic,
                "message_count": self.message_count,
                "ignored_count": self.ignored_count,
                "last_error": self.last_error,
                "spin_thread_alive": self._spin_thread is not None and self._spin_thread.is_alive(),
            }

    def handle_twist(self, msg) -> None:
        with self.lock:
            if not self.enabled:
                self.ignored_count += 1
                return
        try:
            linear_x = float(msg.linear.x)
            linear_y = float(msg.linear.y)
            angular_z = float(msg.angular.z)
            if hasattr(self.controller, "handle_ros_command"):
                result = self.controller.handle_ros_command(linear_x, linear_y, angular_z)
                accepted = bool(result.get("ok", False))
                reason = result.get("message")
            else:
                self.controller.publish(linear_x, linear_y, angular_z)
                accepted = True
                reason = None
            with self.lock:
                if accepted:
                    self.message_count += 1
                    self.last_error = None
                else:
                    self.ignored_count += 1
                    self.last_error = reason
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)

    def _on_msg(self, msg) -> None:
        self.handle_twist(msg)

    def _spin(self) -> None:
        try:
            executor = self.executor
            if executor is not None:
                executor.spin()
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)


class LiveMapSubscriber:
    """Keep the latest ROS OccupancyGrid available as a lightweight PNG preview.

    This subscriber is observational only: it does not publish a ROS topic or
    participate in the control path. The HTTP layer requests a PNG on demand,
    so the browser can poll while mapping without a permanent video stream.
    """

    MAX_PREVIEW_PIXELS = 2_000_000

    def __init__(self, topic: str, setup_paths=None):
        self.topic = topic
        self.setup_paths = tuple(setup_paths or ())
        self.node = None
        self.executor = None
        self.subscription = None
        self.lock = threading.RLock()
        self.width = 0
        self.height = 0
        self.resolution = 0.0
        self.origin = [0.0, 0.0, 0.0]
        self.frame_id = ""
        self.last_received_at: Optional[float] = None
        self.message_count = 0
        self.last_error: Optional[str] = None
        self._pixels = None
        self._spin_thread = None

    @property
    def available(self) -> bool:
        return _ensure_ros_python(self.setup_paths)

    def start(self) -> None:
        if not self.available:
            raise RuntimeError(f"ROS2 Python modules are not available: {_ros_import_error}")
        with self.lock:
            if self.subscription is not None:
                return
            if not rclpy.ok():
                rclpy.init(args=None)
            self.node = rclpy.create_node("peacekeeper_live_map_preview")
            self.executor = SingleThreadedExecutor()
            self.executor.add_node(self.node)
            self.subscription = self.node.create_subscription(
                OccupancyGrid,
                self.topic,
                self._on_map,
                10,
            )
            self._spin_thread = threading.Thread(target=self._spin, daemon=True)
            self._spin_thread.start()

    def shutdown(self) -> None:
        with self.lock:
            node = self.node
            executor = self.executor
            self.node = None
            self.executor = None
            self.subscription = None
            self._pixels = None
        if executor is not None:
            try:
                executor.shutdown()
            except Exception:
                pass
        if node is not None:
            try:
                node.destroy_node()
            except Exception:
                pass

    def status(self) -> dict:
        with self.lock:
            age_s = None
            if self.last_received_at is not None:
                age_s = max(0.0, time.time() - self.last_received_at)
            return {
                "available": self.available,
                "ros_import_error": _ros_import_error,
                "ready": self.subscription is not None,
                "has_map": self._pixels is not None,
                "topic": self.topic,
                "message_count": self.message_count,
                "last_received_at": self.last_received_at,
                "age_s": age_s,
                "width": self.width,
                "height": self.height,
                "resolution": self.resolution,
                "origin": list(self.origin),
                "frame_id": self.frame_id,
                "last_error": self.last_error,
                "spin_thread_alive": self._spin_thread is not None and self._spin_thread.is_alive(),
            }

    def render_png(self) -> bytes:
        with self.lock:
            if self._pixels is None or self.width <= 0 or self.height <= 0:
                raise RuntimeError(f"No OccupancyGrid has been received on {self.topic} yet")
            width = self.width
            height = self.height
            pixels = bytes(self._pixels)
        if width * height > self.MAX_PREVIEW_PIXELS:
            # Keep a potentially large SLAM map observable without turning a
            # preview request into an expensive multi-megabyte transfer.
            step = int(math.ceil(math.sqrt((width * height) / self.MAX_PREVIEW_PIXELS)))
            preview_width = (width + step - 1) // step
            preview_height = (height + step - 1) // step
            pixels = bytes(
                pixels[y * width + x]
                for y in range(0, height, step)
                for x in range(0, width, step)
            )
            width = preview_width
            height = preview_height
        return self._encode_png(width, height, pixels)

    def _on_map(self, msg) -> None:
        try:
            width = int(msg.info.width)
            height = int(msg.info.height)
            values = msg.data
            if width <= 0 or height <= 0 or len(values) != width * height:
                raise ValueError("OccupancyGrid dimensions do not match data length")
            # Match map_saver's PGM convention: occupied black, free white,
            # and unknown gray. It keeps live and saved map previews comparable.
            pixels = bytes(
                205 if int(value) < 0 else max(0, min(254, round(254 * (100 - int(value)) / 100)))
                for value in values
            )
            with self.lock:
                self.width = width
                self.height = height
                self.resolution = float(msg.info.resolution)
                self.origin = [
                    float(msg.info.origin.position.x),
                    float(msg.info.origin.position.y),
                    0.0,
                ]
                self.frame_id = str(msg.header.frame_id)
                self._pixels = pixels
                self.last_received_at = time.time()
                self.message_count += 1
                self.last_error = None
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)

    def _spin(self) -> None:
        try:
            executor = self.executor
            if executor is not None:
                executor.spin()
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)

    @staticmethod
    def _encode_png(width: int, height: int, pixels: bytes) -> bytes:
        rows = b"".join(
            b"\x00" + pixels[offset:offset + width]
            for offset in range(0, width * height, width)
        )

        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6))
            + chunk(b"IEND", b"")
        )


class DirectOdomPublisher:
    """Publish integrated odom/tf for direct-mode SLAM and map saving."""

    def __init__(
        self,
        odom_topic: str,
        odom_frame: str,
        base_frame: str,
        safety: SafetyConfig,
        setup_paths=None,
        feedback_source=None,
        publish_odom: bool = True,
        publish_tf: bool = True,
        period_s: float = 0.05,
        motion_deadband: float = 0.01,
        base_link_frame: str = "base_link",
        linear_x_scale: float = 1.0,
        linear_y_scale: float = 1.0,
        angular_z_scale: float = 1.0,
    ):
        self.odom_topic = odom_topic
        self.odom_frame = odom_frame
        self.base_frame = base_frame
        self.base_link_frame = base_link_frame
        self.safety = safety
        self.setup_paths = tuple(setup_paths or ())
        self.feedback_source = feedback_source
        self.publish_odom_enabled = bool(publish_odom)
        self.publish_tf_enabled = bool(publish_tf)
        self.period_s = max(0.02, float(period_s))
        self.motion_deadband = max(0.0, float(motion_deadband))
        self.motion_scales = {
            "linear_x": float(linear_x_scale),
            "linear_y": float(linear_y_scale),
            "angular_z": float(angular_z_scale),
        }
        self.node = None
        self.executor = None
        self.odom_pub = None
        self.tf_broadcaster = None
        self.static_tf_broadcaster = None
        self.timer = None
        self.lock = threading.RLock()
        self.deadline: Optional[float] = None
        self.current_cmd = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
        self.last_feedback = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
        self.last_motion = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
        self.motion_source = "command_fallback"
        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0
        self.last_tick = time.monotonic()
        self.last_error: Optional[str] = None
        self._feedback_zero_since: Optional[float] = None
        self._spin_thread = None

    @property
    def available(self) -> bool:
        return _ensure_ros_python(self.setup_paths)

    def start(self) -> None:
        if not self.available:
            raise RuntimeError(f"ROS2 Python modules are not available: {_ros_import_error}")
        with self.lock:
            if self.node is not None:
                return
            if not rclpy.ok():
                rclpy.init(args=None)
            self.node = rclpy.create_node("peacekeeper_direct_odom_bridge")
            self.executor = SingleThreadedExecutor()
            self.executor.add_node(self.node)
            if self.publish_odom_enabled:
                self.odom_pub = self.node.create_publisher(Odometry, self.odom_topic, 10)
            if self.publish_tf_enabled:
                self.tf_broadcaster = TransformBroadcaster(self.node)
                self.static_tf_broadcaster = StaticTransformBroadcaster(self.node)
                self._publish_static_base_link_unlocked()
            self.timer = self.node.create_timer(self.period_s, self._on_timer)
            self.last_tick = time.monotonic()
            self._spin_thread = threading.Thread(target=self._spin, daemon=True)
            self._spin_thread.start()

    def update_command(self, linear_x: float, linear_y: float, angular_z: float, ttl_ms: Optional[int] = None) -> None:
        with self.lock:
            self.current_cmd = {
                "linear_x": self._clip(linear_x, self.safety.max_linear_x),
                "linear_y": self._clip(linear_y, self.safety.max_linear_y),
                "angular_z": self._clip(angular_z, self.safety.max_angular_z),
            }
            ttl = ttl_ms if ttl_ms is not None else self.safety.default_ttl_ms
            self.deadline = time.monotonic() + ttl / 1000.0
            self.last_error = None

    def stop(self) -> None:
        with self.lock:
            self.current_cmd = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
            self.deadline = None

    def shutdown(self) -> None:
        with self.lock:
            node = self.node
            executor = self.executor
            self.node = None
            self.executor = None
            self.odom_pub = None
            self.tf_broadcaster = None
            self.static_tf_broadcaster = None
            self.timer = None
        if executor is not None:
            try:
                executor.shutdown()
            except Exception:
                pass
        if node is not None:
            try:
                node.destroy_node()
            except Exception:
                pass

    def status(self) -> dict:
        with self.lock:
            return {
                "available": self.available,
                "ros_import_error": _ros_import_error,
                "ready": self.node is not None,
                "odom_topic": self.odom_topic,
                "odom_frame": self.odom_frame,
                "base_frame": self.base_frame,
                "base_link_frame": self.base_link_frame,
                "publish_odom": self.publish_odom_enabled,
                "publish_tf": self.publish_tf_enabled,
                "period_s": self.period_s,
                "motion_scales": dict(self.motion_scales),
                "feedback_available": self.feedback_source is not None and hasattr(self.feedback_source, "read_motion"),
                "last_command": dict(self.current_cmd),
                "last_feedback": dict(self.last_feedback),
                "last_motion": dict(self.last_motion),
                "motion_source": self.motion_source,
                "ttl_active": self.deadline is not None and time.monotonic() < self.deadline,
                "pose": {"x": self.x, "y": self.y, "yaw": self.yaw},
                "last_error": self.last_error,
                "spin_thread_alive": self._spin_thread is not None and self._spin_thread.is_alive(),
            }

    def _on_timer(self) -> None:
        now = time.monotonic()
        with self.lock:
            dt = max(0.0, min(now - self.last_tick, 0.2))
            self.last_tick = now
            if self.deadline is not None and now > self.deadline:
                self.current_cmd = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
                self.deadline = None
            motion = self._resolve_motion_unlocked()
            self._integrate_pose(dt, motion)
            self._publish_unlocked(motion)

    def _resolve_motion_unlocked(self) -> dict:
        now = time.monotonic()
        command_motion = {
            "linear_x": self._scaled_motion_value("linear_x", self.current_cmd["linear_x"], self.safety.max_linear_x),
            "linear_y": self._scaled_motion_value("linear_y", self.current_cmd["linear_y"], self.safety.max_linear_y),
            "angular_z": self._scaled_motion_value("angular_z", self.current_cmd["angular_z"], self.safety.max_angular_z),
        }
        motion = dict(command_motion)
        self.motion_source = "command_fallback"
        if self.feedback_source is not None and hasattr(self.feedback_source, "read_motion"):
            try:
                feedback = self.feedback_source.read_motion()
                feedback_motion = {
                    "linear_x": self._scaled_motion_value("linear_x", feedback.get("linear_x", 0.0), self.safety.max_linear_x),
                    "linear_y": self._scaled_motion_value("linear_y", feedback.get("linear_y", 0.0), self.safety.max_linear_y),
                    "angular_z": self._scaled_motion_value("angular_z", feedback.get("angular_z", 0.0), self.safety.max_angular_z),
                }
                self.last_feedback = feedback_motion
                if self._feedback_looks_stale(command_motion, feedback_motion):
                    if self._feedback_zero_since is None:
                        self._feedback_zero_since = now
                    if now - self._feedback_zero_since >= max(self.period_s * 2.0, 0.15):
                        self.motion_source = "command_fallback_stale_feedback"
                        self.last_error = "Rosmaster feedback stayed zero while command was active; falling back to commanded motion"
                    else:
                        motion = feedback_motion
                        self.motion_source = "feedback_warmup"
                        self.last_error = None
                else:
                    self._feedback_zero_since = None
                    motion = feedback_motion
                    self.motion_source = "feedback"
                    self.last_error = None
            except Exception as exc:
                self._feedback_zero_since = None
                self.last_error = str(exc)
        self.last_motion = motion
        return motion

    def _integrate_pose(self, dt: float, motion: dict) -> None:
        vx = float(motion["linear_x"])
        vy = float(motion["linear_y"])
        wz = float(motion["angular_z"])
        cos_yaw = math.cos(self.yaw)
        sin_yaw = math.sin(self.yaw)
        self.x += (vx * cos_yaw - vy * sin_yaw) * dt
        self.y += (vx * sin_yaw + vy * cos_yaw) * dt
        self.yaw = self._normalize_angle(self.yaw + wz * dt)

    def _publish_unlocked(self, motion: dict) -> None:
        if self.node is None:
            return
        stamp = self.node.get_clock().now().to_msg()
        qz = math.sin(self.yaw * 0.5)
        qw = math.cos(self.yaw * 0.5)

        if self.publish_tf_enabled and self.tf_broadcaster is not None:
            transform = TransformStamped()
            transform.header.stamp = stamp
            transform.header.frame_id = self.odom_frame
            transform.child_frame_id = self.base_frame
            transform.transform.translation.x = self.x
            transform.transform.translation.y = self.y
            transform.transform.translation.z = 0.0
            transform.transform.rotation.z = qz
            transform.transform.rotation.w = qw
            self.tf_broadcaster.sendTransform(transform)

        if self.publish_odom_enabled and self.odom_pub is not None:
            odom = Odometry()
            odom.header.stamp = stamp
            odom.header.frame_id = self.odom_frame
            odom.child_frame_id = self.base_frame
            odom.pose.pose.position.x = self.x
            odom.pose.pose.position.y = self.y
            odom.pose.pose.orientation.z = qz
            odom.pose.pose.orientation.w = qw
            odom.twist.twist.linear.x = float(motion["linear_x"])
            odom.twist.twist.linear.y = float(motion["linear_y"])
            odom.twist.twist.angular.z = float(motion["angular_z"])
            self.odom_pub.publish(odom)

    def _publish_static_base_link_unlocked(self) -> None:
        if (
            self.node is None
            or self.static_tf_broadcaster is None
            or not self.base_link_frame
            or self.base_link_frame == self.base_frame
        ):
            return
        transform = TransformStamped()
        transform.header.stamp = self.node.get_clock().now().to_msg()
        transform.header.frame_id = self.base_frame
        transform.child_frame_id = self.base_link_frame
        transform.transform.translation.x = 0.0
        transform.transform.translation.y = 0.0
        transform.transform.translation.z = 0.0
        transform.transform.rotation.w = 1.0
        self.static_tf_broadcaster.sendTransform(transform)

    def _spin(self) -> None:
        try:
            executor = self.executor
            if executor is not None:
                executor.spin()
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)

    @staticmethod
    def _clip(value: float, limit: float) -> float:
        return max(-limit, min(limit, float(value)))

    def _sanitize_motion_value(self, value: float, limit: float) -> float:
        clipped = self._clip(value, limit)
        if abs(clipped) < self.motion_deadband:
            return 0.0
        return clipped

    def _scaled_motion_value(self, key: str, value: float, limit: float) -> float:
        scale = self.motion_scales.get(key, 1.0)
        return self._sanitize_motion_value(float(value) * scale, limit)

    @staticmethod
    def _motion_is_zero(motion: dict) -> bool:
        return (
            abs(float(motion["linear_x"])) < 1e-9
            and abs(float(motion["linear_y"])) < 1e-9
            and abs(float(motion["angular_z"])) < 1e-9
        )

    def _feedback_looks_stale(self, command_motion: dict, feedback_motion: dict) -> bool:
        return not self._motion_is_zero(command_motion) and self._motion_is_zero(feedback_motion)

    @staticmethod
    def _normalize_angle(value: float) -> float:
        while value > math.pi:
            value -= 2.0 * math.pi
        while value < -math.pi:
            value += 2.0 * math.pi
        return value
