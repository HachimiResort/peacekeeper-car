"""Runtime mode and shared state."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from uuid import uuid4


class Mode(str, Enum):
    IDLE = "IDLE"
    MANUAL = "MANUAL"
    MAPPING = "MAPPING"
    NAV_PATROL = "NAV_PATROL"
    HAZARD_HOLD = "HAZARD_HOLD"
    LASER_TRACKING = "LASER_TRACKING"
    SAVING_MAP = "SAVING_MAP"
    ERROR = "ERROR"
    ESTOP = "ESTOP"


@dataclass
class RuntimeState:
    run_id: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6])
    mode: Mode = Mode.IDLE
    last_error: Optional[str] = None
    estop: bool = False
    started_at: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def set_mode(self, mode: Mode) -> None:
        with self.lock:
            self.mode = mode
            if mode != Mode.ERROR:
                self.last_error = None

    def set_error(self, message: str) -> None:
        with self.lock:
            self.mode = Mode.ERROR
            self.last_error = message

    def set_estop(self) -> None:
        with self.lock:
            self.mode = Mode.ESTOP
            self.estop = True

    def clear_estop(self) -> None:
        with self.lock:
            self.estop = False
            self.mode = Mode.IDLE
            self.last_error = None
