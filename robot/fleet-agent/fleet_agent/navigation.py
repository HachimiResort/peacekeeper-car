"""Nav2 lifecycle/action helpers for direct-mode navigation."""
from __future__ import annotations

import math
import threading
from importlib import import_module
from pathlib import Path
from typing import Optional, Tuple

from .config import AgentConfig
from .mapping import MappingService
from .process_manager import ProcessManager
from . import ros_control


class NavigationService:
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
        self.current_map_name: Optional[str] = None
        self.current_goal: Optional[dict] = None
        self.action_state = "idle"
        self.last_error: Optional[str] = None
        self.node = None
        self.executor = None
        self.spin_thread = None
        self.initial_pose_pub = None
        self.action_client = None
        self.goal_handle = None
        self._ros_types = None

    def start(self, map_name: str) -> dict:
        safe_name, yaml_path = self._map_yaml(map_name)
        if not yaml_path.exists():
            raise FileNotFoundError(f"Map '{safe_name}' was not found at {yaml_path}")
        current_status = self._nav_process_status()
        if self._is_process_alive(current_status):
            if self.current_map_name == safe_name:
                return self.status(process_status=current_status)
            self.process_manager.stop("nav2")
        command = (
            "ros2 launch yahboomcar_nav navigation_dwa_launch.py "
            f"map:={str(yaml_path)}"
        )
        status = self.process_manager.start_dynamic("nav2", "nav2-dwa", command)
        if not self._is_process_alive(status):
            message = status.get("error") or "Nav2 failed to start"
            with self.lock:
                self.last_error = message
            raise RuntimeError(message)
        with self.lock:
            self.current_map_name = safe_name
            self.action_state = "running"
            self.last_error = None
        return self.status(process_status=status)

    def stop(self) -> dict:
        self.cancel_goal()
        try:
            process_status = self.process_manager.stop("nav2")
        except KeyError:
            process_status = None
        with self.lock:
            self.current_goal = None
            self.current_map_name = None
            self.action_state = "idle"
        return self.status(process_status=process_status)

    def publish_initial_pose(self, x: float, y: float, yaw: float) -> dict:
        self._require_running()
        self._ensure_ros_node()
        PoseWithCovarianceStamped = self._ros_types["PoseWithCovarianceStamped"]
        with self.lock:
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
        self._require_running()
        self._ensure_ros_node()
        NavigateToPose = self._ros_types["NavigateToPose"]
        ActionClient = self._ros_types["ActionClient"]
        with self.lock:
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

        with self.lock:
            self.current_goal = {"x": float(x), "y": float(y), "yaw": float(yaw)}
            self.action_state = "sending"
            self.last_error = None
        future = self.action_client.send_goal_async(goal_msg)
        future.add_done_callback(self._on_goal_response)
        return {"ok": True, "goal": self.current_goal, "action_state": "sending"}

    def cancel_goal(self) -> dict:
        with self.lock:
            goal_handle = self.goal_handle
            if goal_handle is None:
                self.current_goal = None
                if self.action_state not in ("idle", "running"):
                    self.action_state = "canceled"
                return {"ok": True, "action_state": self.action_state}
            self.action_state = "canceling"
        try:
            future = goal_handle.cancel_goal_async()
            future.add_done_callback(self._on_cancel_done)
            return {"ok": True, "action_state": "canceling"}
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)
                self.action_state = "cancel_failed"
            return {"ok": False, "message": str(exc), "action_state": "cancel_failed"}

    def status(self, process_status: Optional[dict] = None) -> dict:
        if process_status is None:
            process_status = self._nav_process_status()
        with self.lock:
            return {
                "process": process_status,
                "current_map": self.current_map_name,
                "current_goal": self.current_goal,
                "action_state": self.action_state,
                "last_error": self.last_error,
                "ready": self.current_map_name is not None and self._is_process_alive(process_status),
                "spin_thread_alive": bool(self.spin_thread and self.spin_thread.is_alive()),
            }

    def shutdown(self) -> None:
        try:
            self.cancel_goal()
        except Exception:
            pass
        with self.lock:
            executor = self.executor
            node = self.node
            self.executor = None
            self.node = None
            self.initial_pose_pub = None
            self.action_client = None
            self.goal_handle = None
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
        with self.lock:
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

        with self.lock:
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

    def _on_goal_response(self, future) -> None:
        try:
            goal_handle = future.result()
            with self.lock:
                if not goal_handle.accepted:
                    self.action_state = "rejected"
                    self.last_error = "NavigateToPose goal was rejected"
                    self.goal_handle = None
                    return
                self.action_state = "active"
                self.goal_handle = goal_handle
            result_future = goal_handle.get_result_async()
            result_future.add_done_callback(self._on_goal_result)
        except Exception as exc:
            with self.lock:
                self.action_state = "failed"
                self.last_error = str(exc)

    def _on_goal_result(self, future) -> None:
        try:
            result = future.result()
            status = int(getattr(result, "status", -1))
            with self.lock:
                self.goal_handle = None
                self.action_state = "succeeded" if status == 4 else f"finished:{status}"
                self.last_error = None if status == 4 else f"NavigateToPose finished with status {status}"
        except Exception as exc:
            with self.lock:
                self.goal_handle = None
                self.action_state = "failed"
                self.last_error = str(exc)

    def _on_cancel_done(self, future) -> None:
        try:
            future.result()
            with self.lock:
                self.goal_handle = None
                self.current_goal = None
                self.action_state = "canceled"
                self.last_error = None
        except Exception as exc:
            with self.lock:
                self.action_state = "cancel_failed"
                self.last_error = str(exc)
