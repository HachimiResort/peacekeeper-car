"""ROS2 /cmd_vel publisher/subscriber helpers with TTL safety."""
from __future__ import annotations

import threading
import time
import os
import shlex
import subprocess
import sys
from importlib import import_module
from typing import Optional

from .config import SafetyConfig

rclpy = None
Twist = None
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
    global rclpy, Twist, _ros_import_error
    if rclpy is not None and Twist is not None:
        return True
    try:
        _source_ros_environment(setup_paths)
        rclpy = import_module("rclpy")
        geometry_msgs = import_module("geometry_msgs.msg")
        Twist = geometry_msgs.Twist
        _ros_import_error = None
        return True
    except Exception as exc:  # pragma: no cover - depends on ROS runtime.
        _ros_import_error = str(exc)
        rclpy = None
        Twist = None
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
            self.subscription = self.node.create_subscription(Twist, self.topic, self._on_msg, 10)
            self._spin_thread = threading.Thread(target=self._spin, args=(self.node,), daemon=True)
            self._spin_thread.start()

    def stop(self) -> None:
        with self.lock:
            self.enabled = False

    def shutdown(self) -> None:
        with self.lock:
            self.enabled = False
            node = self.node
            self.node = None
            self.subscription = None
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

    def _spin(self, node) -> None:
        try:
            if node is not None:
                rclpy.spin(node)
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)
