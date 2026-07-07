"""Direct Rosmaster_Lib controller for the already-verified serial path."""
from __future__ import annotations

import threading
import time
from typing import Optional

from .config import ControlConfig, SafetyConfig


class RosmasterController:
    """A motion controller compatible with CmdVelPublisher's public methods.

    This backend is for the confirmed working path:

    Rosmaster_Lib -> /dev/myserial -> car.

    It does not publish ROS odometry or TF. In direct mode, fleet-agent owns
    command arbitration and this class is only the Rosmaster_Lib adapter.
    """

    def __init__(self, control: ControlConfig, safety: SafetyConfig, bot_factory=None):
        self.control = control
        self.safety = safety
        self.bot_factory = bot_factory
        self.bot = None
        self.lock = threading.RLock()
        self.deadline: Optional[float] = None
        self.last_command = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
        self.last_state = 0
        self.last_speed = self._speed()
        self.last_publish_ok = False
        self._stop_event = threading.Event()
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog.start()

    def start(self) -> None:
        with self.lock:
            if self.bot is not None:
                return
            if self.bot_factory is not None:
                self.bot = self.bot_factory()
            else:
                from Rosmaster_Lib import Rosmaster

                self.bot = Rosmaster(com=self.control.rosmaster_port, debug=False)
            self._stop_unlocked()

    def publish(self, linear_x: float, linear_y: float, angular_z: float, ttl_ms: Optional[int] = None) -> None:
        self.start()
        ttl = ttl_ms if ttl_ms is not None else self.safety.default_ttl_ms
        clipped = {
            "linear_x": self._clip(linear_x, self.safety.max_linear_x),
            "linear_y": self._clip(linear_y, self.safety.max_linear_y),
            "angular_z": self._clip(angular_z, self.safety.max_angular_z),
        }
        with self.lock:
            state = self._state_from_twist(**clipped)
            speed = self._speed_from_twist(**clipped)
            if self.control.direct_motion_mode == "run_state":
                if state == 0:
                    self._stop_unlocked()
                else:
                    self.bot.set_car_run(state, speed)
            elif state == 0:
                self._stop_unlocked()
            else:
                self.bot.set_car_motion(
                    clipped["linear_x"],
                    clipped["linear_y"],
                    clipped["angular_z"],
                )
            self.last_state = state
            self.last_speed = speed
            self.last_command = clipped
            self.deadline = time.monotonic() + ttl / 1000.0
            self.last_publish_ok = True

    def stop(self) -> None:
        self.start()
        with self.lock:
            self._stop_unlocked()
            self.last_command = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
            self.last_state = 0
            self.deadline = None
            self.last_publish_ok = True

    def shutdown(self) -> None:
        self._stop_event.set()
        try:
            self.stop()
        except Exception:
            pass

    def status(self) -> dict:
        return {
            "available": self.bot is not None,
            "publisher_ready": self.bot is not None,
            "mock_mode": False,
            "backend": "rosmaster",
            "topic": None,
            "cmd_vel_subscribers": 0,
            "last_publish_ok": self.last_publish_ok,
            "last_command": dict(self.last_command),
            "last_state": self.last_state,
            "last_speed": self.last_speed,
            "ttl_active": self.deadline is not None and time.monotonic() < self.deadline,
            "port": self.control.rosmaster_port,
            "motion_mode": self.control.direct_motion_mode,
        }

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(self.safety.watchdog_period_s)
            with self.lock:
                if self.deadline is None or time.monotonic() <= self.deadline:
                    continue
                try:
                    if self.bot is not None:
                        self._stop_unlocked()
                finally:
                    self.last_command = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
                    self.last_state = 0
                    self.deadline = None

    def _stop_unlocked(self) -> None:
        for func in (
            lambda: self.bot.set_car_run(0, 0),
            lambda: self.bot.set_car_motion(0, 0, 0),
            lambda: self.bot.set_motor(0, 0, 0, 0),
            lambda: self.bot.set_beep(0),
        ):
            try:
                func()
            except Exception:
                pass

    def _state_from_twist(self, linear_x: float, linear_y: float, angular_z: float) -> int:
        values = {
            "x": abs(linear_x),
            "y": abs(linear_y),
            "z": abs(angular_z),
        }
        if max(values.values()) < self.control.direct_deadband:
            return 0
        dominant = max(values, key=values.get)
        if dominant == "z":
            return 5 if angular_z > 0 else 6
        if dominant == "y":
            return 3 if linear_y > 0 else 4
        return 1 if linear_x > 0 else 2

    def _speed_from_twist(self, linear_x: float, linear_y: float, angular_z: float) -> int:
        # Run-state mode is a fallback for hardware that cannot use continuous
        # set_car_motion reliably. The default direct mode uses set_car_motion
        # and preserves combined x/y/yaw Twist commands.
        del linear_x, linear_y, angular_z
        return self._speed()

    def _speed(self) -> int:
        return max(10, min(80, int(self.control.rosmaster_speed)))

    @staticmethod
    def _clip(value: float, limit: float) -> float:
        return max(-limit, min(limit, float(value)))
