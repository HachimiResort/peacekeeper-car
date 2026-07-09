"""Local command arbitration for the direct Rosmaster backend."""
from __future__ import annotations

import threading
import time
from typing import Optional

from .config import SafetyConfig
from .state import Mode, RuntimeState


class CommandArbiter:
    """Single control gate before commands reach the chassis controller."""

    ROS_ALLOWED_MODES = {Mode.LASER_TRACKING, Mode.NAV_PATROL}

    def __init__(
        self,
        controller,
        state: RuntimeState,
        safety: SafetyConfig,
        manual_override_s: float = 0.8,
        motion_sink=None,
    ):
        self.controller = controller
        self.state = state
        self.safety = safety
        self.manual_override_s = manual_override_s
        self.motion_sink = motion_sink
        self.lock = threading.RLock()
        self.manual_override_until = 0.0
        self.accepted_count = 0
        self.rejected_count = 0
        self.last_source: Optional[str] = None
        self.last_reject_reason: Optional[str] = None
        self.last_command = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}

    def handle_manual_command(
        self,
        linear_x: float,
        linear_y: float,
        angular_z: float,
        ttl_ms: Optional[int] = None,
    ) -> dict:
        if self.state.estop:
            return self._reject("manual", "ESTOP is active")

        zero = self._is_zero(linear_x, linear_y, angular_z)
        if self.state.mode == Mode.LASER_TRACKING and not zero:
            return self._reject("manual", "Laser tracking is active; stop tracking before manual control")

        try:
            self.controller.publish(linear_x, linear_y, angular_z, ttl_ms=ttl_ms)
        except Exception as exc:
            return self._reject("manual", str(exc))

        now = time.monotonic()
        with self.lock:
            self.manual_override_until = now + self.manual_override_s
            self._accept_unlocked("manual", linear_x, linear_y, angular_z)
        self._notify_motion(linear_x, linear_y, angular_z, ttl_ms)

        if zero:
            if self.state.mode in (Mode.MANUAL, Mode.NAV_PATROL):
                self.state.set_mode(Mode.IDLE)
        elif self.state.mode in (Mode.IDLE, Mode.NAV_PATROL):
            self.state.set_mode(Mode.MANUAL)

        return self._ok("manual")

    def handle_ros_command(
        self,
        linear_x: float,
        linear_y: float,
        angular_z: float,
        ttl_ms: Optional[int] = None,
    ) -> dict:
        if self.state.estop:
            self._stop_controller()
            return self._reject("ros", "ESTOP is active")

        now = time.monotonic()
        with self.lock:
            if now < self.manual_override_until:
                return self._reject_unlocked("ros", "manual override active")

        if self.state.mode not in self.ROS_ALLOWED_MODES:
            return self._reject("ros", f"ROS /cmd_vel rejected while mode={self.state.mode.value}")

        try:
            self.controller.publish(
                linear_x,
                linear_y,
                angular_z,
                ttl_ms=ttl_ms if ttl_ms is not None else self.safety.default_ttl_ms,
            )
        except Exception as exc:
            return self._reject("ros", str(exc))

        with self.lock:
            self._accept_unlocked("ros", linear_x, linear_y, angular_z)
        self._notify_motion(
            linear_x,
            linear_y,
            angular_z,
            ttl_ms if ttl_ms is not None else self.safety.default_ttl_ms,
        )
        return self._ok("ros")

    def publish(self, linear_x: float, linear_y: float, angular_z: float, ttl_ms: Optional[int] = None) -> dict:
        return self.handle_ros_command(linear_x, linear_y, angular_z, ttl_ms=ttl_ms)

    def enable_ros_cmd_vel(self, mode: Mode = Mode.NAV_PATROL) -> dict:
        if mode not in self.ROS_ALLOWED_MODES:
            return self._reject("ros", f"mode={mode.value} cannot own ROS /cmd_vel")
        if self.state.estop:
            return self._reject("ros", "ESTOP is active")
        self.state.set_mode(mode)
        with self.lock:
            self.manual_override_until = 0.0
            self.last_reject_reason = None
        return self._ok("ros")

    def stop(self) -> None:
        self._stop_controller()
        self._notify_stop()
        with self.lock:
            self.manual_override_until = 0.0
            self.last_command = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
            self.last_source = "stop"
        if not self.state.estop and self.state.mode in (Mode.MANUAL, Mode.LASER_TRACKING, Mode.NAV_PATROL):
            self.state.set_mode(Mode.IDLE)

    def shutdown(self) -> None:
        self.controller.shutdown()

    def status(self) -> dict:
        controller_status = self.controller.status()
        with self.lock:
            return {
                **controller_status,
                "arbiter": {
                    "ros_allowed_modes": [mode.value for mode in sorted(self.ROS_ALLOWED_MODES, key=lambda item: item.value)],
                    "accepted_count": self.accepted_count,
                    "rejected_count": self.rejected_count,
                    "last_source": self.last_source,
                    "last_reject_reason": self.last_reject_reason,
                    "last_command": dict(self.last_command),
                    "manual_override_active": time.monotonic() < self.manual_override_until,
                },
            }

    def _stop_controller(self) -> None:
        try:
            self.controller.stop()
        except Exception:
            pass

    def _notify_motion(self, linear_x: float, linear_y: float, angular_z: float, ttl_ms: Optional[int]) -> None:
        if self.motion_sink is None:
            return
        try:
            self.motion_sink.update_command(linear_x, linear_y, angular_z, ttl_ms=ttl_ms)
        except Exception:
            pass

    def _notify_stop(self) -> None:
        if self.motion_sink is None:
            return
        try:
            self.motion_sink.stop()
        except Exception:
            pass

    def _accept_unlocked(self, source: str, linear_x: float, linear_y: float, angular_z: float) -> None:
        self.accepted_count += 1
        self.last_source = source
        self.last_reject_reason = None
        self.last_command = {
            "linear_x": float(linear_x),
            "linear_y": float(linear_y),
            "angular_z": float(angular_z),
        }

    def _reject(self, source: str, reason: str) -> dict:
        with self.lock:
            return self._reject_unlocked(source, reason)

    def _reject_unlocked(self, source: str, reason: str) -> dict:
        self.rejected_count += 1
        self.last_source = source
        self.last_reject_reason = reason
        return {"ok": False, "source": source, "message": reason}

    @staticmethod
    def _ok(source: str) -> dict:
        return {"ok": True, "source": source}

    @staticmethod
    def _is_zero(linear_x: float, linear_y: float, angular_z: float) -> bool:
        return abs(linear_x) < 1e-9 and abs(linear_y) < 1e-9 and abs(angular_z) < 1e-9
