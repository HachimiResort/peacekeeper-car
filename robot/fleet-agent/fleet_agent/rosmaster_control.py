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
        self.last_feedback = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
        self.last_feedback_error: Optional[str] = None
        self.feedback_thread_started = False
        self.last_publish_ok = False
        self.last_light_command = {"left": False, "right": False, "duration_ms": 0}
        self.last_buzzer_command = {"enabled": False, "duration_ms": 0}
        self._buzzer_timer: Optional[threading.Timer] = None
        self._buzzer_deadline: Optional[float] = None
        self._buzzer_generation = 0
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
            if hasattr(self.bot, "create_receive_threading"):
                self.bot.create_receive_threading()
                self.feedback_thread_started = True
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

    def control_lights(self, left: bool, right: bool, duration_ms: int = 0) -> dict:
        """Set iCAR headlights through the Rosmaster-owned serial connection.

        The light command is not part of the public Rosmaster_Lib API, so this
        writes its documented 0x70 protocol frame through the existing
        Rosmaster serial handle.  Holding ``lock`` keeps each whole command
        sequence from interleaving with motion commands on /dev/myserial.
        """
        duration = int(duration_ms)
        if not 0 <= duration <= 0xFF:
            raise ValueError("duration_ms must be between 0 and 255")

        self.start()
        frames = self._light_frames(bool(left), bool(right), duration)
        with self.lock:
            serial_port = getattr(self.bot, "ser", None)
            if serial_port is None or not callable(getattr(serial_port, "write", None)):
                raise RuntimeError("Rosmaster_Lib serial handle is not available")
            for frame in frames:
                serial_port.write(frame)
            if callable(getattr(serial_port, "flush", None)):
                serial_port.flush()
            self.last_light_command = {
                "left": bool(left),
                "right": bool(right),
                "duration_ms": duration,
            }

        return {
            **self.last_light_command,
            "frames": [frame.hex().upper() for frame in frames],
        }

    def control_buzzer(self, enabled: bool, duration_ms: int = 0) -> dict:
        """Set the onboard buzzer through Rosmaster_Lib's shared serial link.

        Rosmaster_Lib versions do not consistently expose the firmware's
        timed-beep argument.  The agent therefore sends an explicit on/off
        command and owns the optional auto-silence timer.
        """
        duration = int(duration_ms)
        if not 0 <= duration <= 60000:
            raise ValueError("duration_ms must be between 0 and 60000")

        self.start()
        with self.lock:
            self._cancel_buzzer_timer_unlocked()
            active = bool(enabled)
            self._set_beep_unlocked(active)
            self.last_buzzer_command = {
                "enabled": active,
                "duration_ms": duration if active else 0,
            }
            if active and duration:
                generation = self._buzzer_generation
                self._buzzer_deadline = time.monotonic() + duration / 1000.0
                timer = threading.Timer(duration / 1000.0, self._auto_silence_buzzer, args=(generation,))
                timer.daemon = True
                self._buzzer_timer = timer
                timer.start()
            return self._buzzer_status_unlocked()

    def shutdown(self) -> None:
        self._stop_event.set()
        try:
            self.stop()
        except Exception:
            pass

    def read_motion(self) -> dict:
        self.start()
        with self.lock:
            if self.bot is None or not hasattr(self.bot, "get_motion_data"):
                self.last_feedback_error = "Rosmaster motion feedback is not available"
                raise RuntimeError(self.last_feedback_error)
            try:
                values = tuple(self.bot.get_motion_data())
                if len(values) != 3:
                    raise RuntimeError(f"Unexpected motion feedback payload: {values!r}")
                motion = {
                    "linear_x": float(values[0]),
                    "linear_y": float(values[1]),
                    "angular_z": float(values[2]),
                }
                self.last_feedback = motion
                self.last_feedback_error = None
                return motion
            except Exception as exc:
                self.last_feedback_error = str(exc)
                raise

    def status(self) -> dict:
        with self.lock:
            buzzer = self._buzzer_status_unlocked()
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
            "motion_feedback_available": self.bot is not None and hasattr(self.bot, "get_motion_data"),
            "feedback_thread_started": self.feedback_thread_started,
            "last_feedback": dict(self.last_feedback),
            "last_feedback_error": self.last_feedback_error,
            "last_light_command": dict(self.last_light_command),
            "buzzer": buzzer,
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
        ):
            try:
                func()
            except Exception:
                pass
        self._cancel_buzzer_timer_unlocked()
        try:
            self._set_beep_unlocked(False)
        except Exception:
            pass
        self.last_buzzer_command = {"enabled": False, "duration_ms": 0}

    def _set_beep_unlocked(self, enabled: bool) -> None:
        set_beep = getattr(self.bot, "set_beep", None)
        if not callable(set_beep):
            raise RuntimeError("Rosmaster_Lib buzzer control is not available")
        set_beep(1 if enabled else 0)

    def _cancel_buzzer_timer_unlocked(self) -> None:
        self._buzzer_generation += 1
        if self._buzzer_timer is not None:
            self._buzzer_timer.cancel()
        self._buzzer_timer = None
        self._buzzer_deadline = None

    def _auto_silence_buzzer(self, generation: int) -> None:
        with self.lock:
            if generation != self._buzzer_generation:
                return
            self._buzzer_timer = None
            self._buzzer_deadline = None
            try:
                self._set_beep_unlocked(False)
            except Exception:
                pass
            self.last_buzzer_command = {"enabled": False, "duration_ms": 0}

    def _buzzer_status_unlocked(self) -> dict:
        remaining_ms = 0
        if self._buzzer_deadline is not None:
            remaining_ms = max(0, int((self._buzzer_deadline - time.monotonic()) * 1000))
        return {
            **self.last_buzzer_command,
            "remaining_ms": remaining_ms,
        }

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

    @classmethod
    def _light_frames(cls, left: bool, right: bool, duration_ms: int) -> list[bytes]:
        if duration_ms and left and right:
            return [cls._light_frame(action=3, light=0, duration_ms=duration_ms)]
        return [
            cls._light_frame(action=1 if left else 2, light=1, duration_ms=duration_ms if left else 0),
            cls._light_frame(action=1 if right else 2, light=2, duration_ms=duration_ms if right else 0),
        ]

    @staticmethod
    def _light_frame(action: int, light: int, duration_ms: int) -> bytes:
        # Firmware protocol: FF FC 06 70 <action> <left=1|right=2> <ms> <sum>.
        payload = bytes((0x06, 0x70, action, light, duration_ms))
        return b"\xFF\xFC" + payload + bytes((sum(payload) & 0xFF,))

    @staticmethod
    def _clip(value: float, limit: float) -> float:
        return max(-limit, min(limit, float(value)))
