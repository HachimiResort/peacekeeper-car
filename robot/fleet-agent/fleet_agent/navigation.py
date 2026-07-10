"""Nav2 lifecycle/action helpers for direct-mode navigation."""
from __future__ import annotations

import math
import shlex
import threading
import time
from importlib import import_module
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import ros_control
from .config import AgentConfig
from .mapping import MappingService
from .process_manager import ProcessManager


class NavigationService:
    TERMINAL_STATES = {"succeeded", "canceled", "aborted", "rejected", "failed", "stopped"}
    ACTION_STATUS = {
        0: "unknown",
        1: "accepted",
        2: "active",
        3: "canceling",
        4: "succeeded",
        5: "canceled",
        6: "aborted",
    }

    def __init__(
        self,
        config: AgentConfig,
        process_manager: ProcessManager,
        setup_paths: Tuple[str, str],
    ):
        self.config = config
        self.process_manager = process_manager
        self.setup_paths = setup_paths
        self.maps_dir = Path(config.data_dir) / "maps"
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.current_map_name: Optional[str] = None
        self.current_goal: Optional[dict] = None
        self.action_state = "idle"
        self.last_error: Optional[str] = None
        self.last_result: Optional[dict] = None
        self.last_feedback: Optional[dict] = None
        self.node = None
        self.executor = None
        self.spin_thread = None
        self.initial_pose_pub = None
        self.action_client = None
        self.goal_handle = None
        self.goal_future = None
        self._ros_types = None
        self._goal_sequence = 0
        self._active_goal_id: Optional[str] = None
        self._cancel_requested = False
        self._cancel_sent = False
        self._goal_results: Dict[str, dict] = {}

    def start(self, map_name: str) -> dict:
        safe_name, yaml_path = self._map_yaml(map_name)
        if not yaml_path.exists():
            raise FileNotFoundError(f"Map '{safe_name}' was not found at {yaml_path}")
        current_status = self._nav_process_status()
        if self._is_process_alive(current_status):
            if self.current_map_name == safe_name:
                return self.status(process_status=current_status)
            self.cancel_goal(wait_timeout_s=1.5)
            self.process_manager.stop("nav2")
        command = (
            "ros2 launch yahboomcar_nav navigation_dwa_launch.py "
            f"map:={shlex.quote(str(yaml_path))}"
        )
        status = self.process_manager.start_dynamic("nav2", "nav2-dwa", command)
        if not self._is_process_alive(status):
            message = status.get("error") or "Nav2 failed to start"
            with self.condition:
                self.last_error = message
                self.action_state = "failed"
            raise RuntimeError(message)
        with self.condition:
            self.current_map_name = safe_name
            self.current_goal = None
            self.action_state = "running"
            self.last_error = None
            self.last_feedback = None
            self.condition.notify_all()
        return self.status(process_status=status)

    def stop(self) -> dict:
        self.cancel_goal(wait_timeout_s=1.5)
        try:
            process_status = self.process_manager.stop("nav2")
        except KeyError:
            process_status = None
        with self.condition:
            if self._active_goal_id is not None:
                self._finish_goal_unlocked(self._active_goal_id, "stopped", "Nav2 was stopped")
            self.current_goal = None
            self.current_map_name = None
            self.action_state = "idle"
            self.last_feedback = None
            self.condition.notify_all()
        return self.status(process_status=process_status)

    def publish_initial_pose(self, x: float, y: float, yaw: float) -> dict:
        self._validate_pose(x, y, yaw)
        self._require_running()
        self._ensure_ros_node()
        PoseWithCovarianceStamped = self._ros_types["PoseWithCovarianceStamped"]
        with self.condition:
            if self.initial_pose_pub is None:
                self.initial_pose_pub = self.node.create_publisher(
                    PoseWithCovarianceStamped,
                    "/initialpose",
                    10,
                )
            message = PoseWithCovarianceStamped()
            message.header.frame_id = "map"
            message.header.stamp = self.node.get_clock().now().to_msg()
            message.pose.pose.position.x = float(x)
            message.pose.pose.position.y = float(y)
            message.pose.pose.position.z = 0.0
            self._set_yaw(message.pose.pose.orientation, yaw)
            message.pose.covariance[0] = 0.25
            message.pose.covariance[7] = 0.25
            message.pose.covariance[35] = 0.0685
            self.initial_pose_pub.publish(message)
            self.last_error = None
        return {"ok": True, "initial_pose": {"x": x, "y": y, "yaw": yaw}}

    def send_goal(self, x: float, y: float, yaw: float) -> dict:
        self._validate_pose(x, y, yaw)
        self._require_running()
        self._ensure_ros_node()
        NavigateToPose = self._ros_types["NavigateToPose"]
        ActionClient = self._ros_types["ActionClient"]
        with self.condition:
            if self._active_goal_id is not None:
                raise RuntimeError(
                    f"Navigation goal {self._active_goal_id} is still {self.action_state}; cancel it first"
                )
            if self.action_client is None:
                self.action_client = ActionClient(
                    self.node,
                    NavigateToPose,
                    "/navigate_to_pose",
                )
        if not self.action_client.wait_for_server(timeout_sec=5.0):
            raise RuntimeError("/navigate_to_pose action server is not ready")

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id = "map"
        goal_msg.pose.header.stamp = self.node.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = float(x)
        goal_msg.pose.pose.position.y = float(y)
        goal_msg.pose.pose.position.z = 0.0
        self._set_yaw(goal_msg.pose.pose.orientation, yaw)

        with self.condition:
            self._goal_sequence += 1
            goal_id = f"nav-{self._goal_sequence}"
            self._active_goal_id = goal_id
            self.current_goal = {
                "id": goal_id,
                "x": float(x),
                "y": float(y),
                "yaw": float(yaw),
                "sent_at": time.time(),
            }
            self.action_state = "sending"
            self.last_error = None
            self.last_feedback = None
            self.goal_handle = None
            self._cancel_requested = False
            self._cancel_sent = False
        try:
            future = self.action_client.send_goal_async(
                goal_msg,
                feedback_callback=lambda message, token=goal_id: self._on_goal_feedback(token, message),
            )
        except Exception as exc:
            self._finish_goal(goal_id, "failed", str(exc))
            raise
        with self.condition:
            self.goal_future = future
        future.add_done_callback(lambda result, token=goal_id: self._on_goal_response(token, result))
        return {
            "ok": True,
            "goal_id": goal_id,
            "goal": dict(self.current_goal),
            "action_state": "sending",
        }

    def cancel_goal(self, wait_timeout_s: float = 0.0) -> dict:
        with self.condition:
            goal_id = self._active_goal_id
            if goal_id is None:
                return {
                    "ok": True,
                    "goal_id": None,
                    "action_state": self.action_state,
                    "result": self.last_result,
                }
            self._cancel_requested = True
            self.action_state = "canceling"
            goal_handle = self.goal_handle
            self.condition.notify_all()
        if goal_handle is not None:
            self._request_cancel(goal_id, goal_handle)
        if wait_timeout_s > 0:
            result = self.wait_for_goal(goal_id, timeout_s=wait_timeout_s)
            return {
                "ok": result.get("state") in self.TERMINAL_STATES,
                "goal_id": goal_id,
                "action_state": result.get("state"),
                "result": result,
            }
        return {"ok": True, "goal_id": goal_id, "action_state": "canceling"}

    def wait_for_goal(self, goal_id: str, timeout_s: Optional[float] = None) -> dict:
        deadline = None if timeout_s is None else time.monotonic() + max(0.0, timeout_s)
        with self.condition:
            while True:
                result = self._goal_results.get(goal_id)
                if result is not None:
                    return dict(result)
                if self._active_goal_id != goal_id:
                    return {"goal_id": goal_id, "state": "superseded", "error": None}
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return {"goal_id": goal_id, "state": "timeout", "error": None}
                self.condition.wait(timeout=remaining)

    def status(self, process_status: Optional[dict] = None) -> dict:
        if process_status is None:
            process_status = self._nav_process_status()
        with self.condition:
            return {
                "process": process_status,
                "current_map": self.current_map_name,
                "current_goal": dict(self.current_goal) if self.current_goal else None,
                "active_goal_id": self._active_goal_id,
                "action_state": self.action_state,
                "last_result": dict(self.last_result) if self.last_result else None,
                "feedback": dict(self.last_feedback) if self.last_feedback else None,
                "last_error": self.last_error,
                "ready": self.current_map_name is not None and self._is_process_alive(process_status),
                "spin_thread_alive": bool(self.spin_thread and self.spin_thread.is_alive()),
            }

    def shutdown(self) -> None:
        try:
            self.cancel_goal(wait_timeout_s=0.5)
        except Exception:
            pass
        with self.condition:
            executor = self.executor
            node = self.node
            self.executor = None
            self.node = None
            self.initial_pose_pub = None
            self.action_client = None
            self.goal_handle = None
            self.goal_future = None
            if self._active_goal_id is not None:
                self._finish_goal_unlocked(self._active_goal_id, "stopped", "Navigation client shut down")
        try:
            if executor is not None:
                executor.shutdown()
        except Exception:
            pass
        try:
            if node is not None:
                node.destroy_node()
        except Exception:
            pass

    def _map_yaml(self, map_name: str) -> Tuple[str, Path]:
        safe_name = MappingService._safe_name(map_name)
        return safe_name, self.maps_dir / f"{safe_name}.yaml"

    def _require_running(self) -> None:
        status = self._nav_process_status()
        if self.current_map_name is None or not self._is_process_alive(status):
            raise RuntimeError("Nav2 is not running; call /api/navigation/start first")

    def _nav_process_status(self) -> Optional[dict]:
        try:
            return self.process_manager.status().get("nav2")
        except Exception:
            return None

    @staticmethod
    def _is_process_alive(status: Optional[dict]) -> bool:
        if not status:
            return False
        return status.get("status") in {"starting", "running"}

    def _ensure_ros_node(self) -> None:
        with self.condition:
            if self.node is not None:
                return
        if not ros_control._ensure_ros_python(self.setup_paths):
            raise RuntimeError(f"ROS2 Python modules are not available: {ros_control._ros_import_error}")

        try:
            geometry_msgs = import_module("geometry_msgs.msg")
            nav2_msgs = import_module("nav2_msgs.action")
            rclpy_action = import_module("rclpy.action")
        except Exception as exc:
            raise RuntimeError(f"Navigation ROS imports failed: {exc}")

        with self.condition:
            if self.node is not None:
                return
            if not ros_control.rclpy.ok():
                ros_control.rclpy.init(args=None)
            self.node = ros_control.rclpy.create_node("peacekeeper_navigation_client")
            self.executor = ros_control.SingleThreadedExecutor()
            self.executor.add_node(self.node)
            self._ros_types = {
                "PoseWithCovarianceStamped": geometry_msgs.PoseWithCovarianceStamped,
                "NavigateToPose": nav2_msgs.NavigateToPose,
                "ActionClient": rclpy_action.ActionClient,
            }
            self.spin_thread = threading.Thread(target=self.executor.spin, daemon=True)
            self.spin_thread.start()

    @staticmethod
    def _set_yaw(orientation, yaw: float) -> None:
        half = float(yaw) * 0.5
        orientation.x = 0.0
        orientation.y = 0.0
        orientation.z = math.sin(half)
        orientation.w = math.cos(half)

    @staticmethod
    def _validate_pose(x: float, y: float, yaw: float) -> None:
        if not all(math.isfinite(float(value)) for value in (x, y, yaw)):
            raise ValueError("Navigation pose values must be finite numbers")

    def _on_goal_response(self, goal_id: str, future) -> None:
        try:
            goal_handle = future.result()
        except Exception as exc:
            self._finish_goal(goal_id, "failed", str(exc))
            return

        with self.condition:
            if self._active_goal_id != goal_id:
                stale = True
                cancel_requested = False
            else:
                stale = False
                if not goal_handle.accepted:
                    self._finish_goal_unlocked(goal_id, "rejected", "NavigateToPose goal was rejected")
                    return
                self.goal_handle = goal_handle
                self.goal_future = None
                cancel_requested = self._cancel_requested
                self.action_state = "canceling" if cancel_requested else "active"
                self.condition.notify_all()
        if stale:
            if goal_handle.accepted:
                try:
                    goal_handle.cancel_goal_async()
                except Exception:
                    pass
            return

        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(
            lambda result, token=goal_id: self._on_goal_result(token, result)
        )
        if cancel_requested:
            self._request_cancel(goal_id, goal_handle)

    def _request_cancel(self, goal_id: str, goal_handle) -> None:
        with self.condition:
            if self._active_goal_id != goal_id or self._cancel_sent:
                return
            self._cancel_sent = True
        try:
            future = goal_handle.cancel_goal_async()
            future.add_done_callback(
                lambda result, token=goal_id: self._on_cancel_response(token, result)
            )
        except Exception as exc:
            with self.condition:
                if self._active_goal_id == goal_id:
                    self._cancel_sent = False
                    self.action_state = "active"
                    self.last_error = f"Failed to request goal cancellation: {exc}"
                    self.condition.notify_all()

    def _on_cancel_response(self, goal_id: str, future) -> None:
        try:
            response = future.result()
            accepted = bool(getattr(response, "goals_canceling", []))
        except Exception as exc:
            accepted = False
            error = str(exc)
        else:
            error = "Nav2 did not accept goal cancellation" if not accepted else None
        with self.condition:
            if self._active_goal_id != goal_id:
                return
            if not accepted:
                self._cancel_requested = False
                self._cancel_sent = False
                self.action_state = "active"
                self.last_error = error
            self.condition.notify_all()

    def _on_goal_result(self, goal_id: str, future) -> None:
        try:
            result = future.result()
            status_code = int(getattr(result, "status", -1))
            state = self.ACTION_STATUS.get(status_code, f"finished:{status_code}")
            error = None if state == "succeeded" else f"NavigateToPose finished with status {state}"
            self._finish_goal(goal_id, state, error, status_code=status_code)
        except Exception as exc:
            self._finish_goal(goal_id, "failed", str(exc))

    def _on_goal_feedback(self, goal_id: str, message) -> None:
        feedback = getattr(message, "feedback", message)
        value = {
            "goal_id": goal_id,
            "distance_remaining": self._optional_float(feedback, "distance_remaining"),
            "number_of_recoveries": self._optional_int(feedback, "number_of_recoveries"),
        }
        with self.condition:
            if self._active_goal_id != goal_id:
                return
            self.last_feedback = value

    def _finish_goal(
        self,
        goal_id: str,
        state: str,
        error: Optional[str],
        status_code: Optional[int] = None,
    ) -> None:
        with self.condition:
            self._finish_goal_unlocked(goal_id, state, error, status_code=status_code)

    def _finish_goal_unlocked(
        self,
        goal_id: str,
        state: str,
        error: Optional[str],
        status_code: Optional[int] = None,
    ) -> None:
        if self._active_goal_id != goal_id:
            return
        result = {
            "goal_id": goal_id,
            "state": state,
            "status_code": status_code,
            "error": error,
            "finished_at": time.time(),
        }
        self._goal_results[goal_id] = result
        while len(self._goal_results) > 32:
            self._goal_results.pop(next(iter(self._goal_results)))
        self.last_result = result
        self.last_error = error
        self.action_state = state
        self._active_goal_id = None
        self.goal_handle = None
        self.goal_future = None
        self._cancel_requested = False
        self._cancel_sent = False
        self.condition.notify_all()

    @staticmethod
    def _optional_float(value, name: str) -> Optional[float]:
        item = getattr(value, name, None)
        return None if item is None else float(item)

    @staticmethod
    def _optional_int(value, name: str) -> Optional[int]:
        item = getattr(value, name, None)
        return None if item is None else int(item)
