"""Sequential patrol route execution on top of NavigationService."""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    import yaml
except Exception:  # pragma: no cover - installed on the car via requirements.txt.
    yaml = None

from .config import PatrolConfig
from .mapping import MappingService
from .navigation import NavigationService


class PatrolService:
    ACTIVE_STATES = {"running", "pausing", "paused", "canceling"}

    def __init__(
        self,
        config: PatrolConfig,
        navigation: NavigationService,
        before_goal: Callable[[], dict],
        stop_motion: Callable[[], None],
        routes: Optional[dict] = None,
    ):
        self.config = config
        self.navigation = navigation
        self.before_goal = before_goal
        self.stop_motion = stop_motion
        self.lock = threading.RLock()
        self.routes_file = Path(config.routes_file).expanduser()
        self.routes: Dict[str, dict] = {}
        self.routes_error: Optional[str] = None
        self.state = "idle"
        self.current_route: Optional[dict] = None
        self.current_index: Optional[int] = None
        self.current_goal_id: Optional[str] = None
        self.cycle = 0
        self.last_error: Optional[str] = None
        self.cancel_reason: Optional[str] = None
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None
        self.worker: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        if routes is None:
            self.reload_routes()
        else:
            self._set_routes(routes)

    def reload_routes(self) -> dict:
        if not self.routes_file.exists():
            with self.lock:
                self.routes = {}
                self.routes_error = f"Patrol routes file was not found: {self.routes_file}"
            return self.routes_status()
        try:
            text = self.routes_file.read_text(encoding="utf-8")
            raw = self._load_routes_document(text)
            self._set_routes(raw.get("routes", raw))
            with self.lock:
                self.routes_error = None
        except Exception as exc:
            with self.lock:
                self.routes_error = str(exc)
            raise
        return self.routes_status()

    @staticmethod
    def _load_routes_document(text: str) -> dict:
        if yaml is not None:
            return yaml.safe_load(text) or {}
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else {}
        except Exception:
            meaningful = [
                line.split("#", 1)[0].strip()
                for line in text.splitlines()
            ]
            meaningful = [line for line in meaningful if line]
            if meaningful in (["routes: {}"], ["{}"]):
                return {"routes": {}}
            raise RuntimeError(
                "PyYAML is required for non-empty YAML patrol routes; install requirements.txt"
            )

    def routes_status(self) -> dict:
        with self.lock:
            return {
                "routes_file": str(self.routes_file),
                "routes_file_exists": self.routes_file.exists(),
                "routes_error": self.routes_error,
                "routes": {
                    name: self._copy_route(route)
                    for name, route in self.routes.items()
                },
            }

    def build_route(
        self,
        route_name: Optional[str] = None,
        map_name: Optional[str] = None,
        points: Optional[List[dict]] = None,
        loop: Optional[bool] = None,
    ) -> dict:
        if route_name:
            with self.lock:
                source = self.routes.get(route_name)
                if source is None:
                    raise KeyError(f"Unknown patrol route: {route_name}")
                route = self._copy_route(source)
            if points:
                raise ValueError("Use either route_name or inline points, not both")
            if map_name:
                route["map_name"] = MappingService._safe_name(map_name)
            if loop is not None:
                route["loop"] = bool(loop)
            return route

        if not points:
            raise ValueError("Patrol requires route_name or at least one inline point")
        raw = {
            "map_name": map_name,
            "loop": bool(loop) if loop is not None else False,
            "points": points,
        }
        return self._normalize_route("draft", raw)

    def start(self, route: dict) -> dict:
        nav_status = self.navigation.status()
        if not nav_status.get("ready"):
            raise RuntimeError("Nav2 is not running; start navigation before patrol")
        if nav_status.get("active_goal_id"):
            raise RuntimeError(
                f"Navigation goal {nav_status['active_goal_id']} is already active; cancel it before patrol"
            )
        route_map = route.get("map_name")
        current_map = nav_status.get("current_map")
        if route_map and route_map != current_map:
            raise RuntimeError(
                f"Patrol route uses map '{route_map}', but Nav2 is running map '{current_map}'"
            )
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError(f"Patrol is already {self.state}")
            self.stop_event = threading.Event()
            self.pause_event = threading.Event()
            self.current_route = self._copy_route(route)
            self.current_index = 0
            self.current_goal_id = None
            self.cycle = 0
            self.last_error = None
            self.cancel_reason = None
            self.started_at = time.time()
            self.completed_at = None
            self.state = "running"
            self.worker = threading.Thread(target=self._run, name="peacekeeper-patrol", daemon=True)
            self.worker.start()
        return self.status()

    def pause(self) -> dict:
        with self.lock:
            if self.state not in {"running", "pausing"}:
                raise RuntimeError(f"Cannot pause patrol while state={self.state}")
            self.pause_event.set()
            self.state = "pausing"
        self.navigation.cancel_goal()
        self._safe_stop_motion()
        return self.status()

    def resume(self) -> dict:
        with self.lock:
            if self.state != "paused":
                raise RuntimeError(f"Cannot resume patrol while state={self.state}")
            self.pause_event.clear()
            self.state = "running"
        return self.status()

    def cancel(self, reason: str = "operator_cancel", wait_timeout_s: float = 0.0) -> dict:
        with self.lock:
            active = self.worker is not None and self.worker.is_alive()
            if not active:
                return self.status()
            self.cancel_reason = reason
            self.state = "canceling"
            self.stop_event.set()
            self.pause_event.clear()
            worker = self.worker
        self.navigation.cancel_goal()
        self._safe_stop_motion()
        if wait_timeout_s > 0 and worker is not None and worker is not threading.current_thread():
            worker.join(timeout=wait_timeout_s)
        return self.status()

    def is_active(self) -> bool:
        with self.lock:
            return bool(self.worker and self.worker.is_alive() and self.state in self.ACTIVE_STATES)

    def status(self) -> dict:
        with self.lock:
            route = self._copy_route(self.current_route) if self.current_route else None
            total = len(route["points"]) if route else 0
            point = None
            if route and self.current_index is not None and 0 <= self.current_index < total:
                point = dict(route["points"][self.current_index])
            return {
                "state": self.state,
                "active": bool(self.worker and self.worker.is_alive() and self.state in self.ACTIVE_STATES),
                "route": route,
                "route_name": route.get("name") if route else None,
                "current_index": self.current_index,
                "current_point": point,
                "current_goal_id": self.current_goal_id,
                "total_points": total,
                "cycle": self.cycle,
                "pause_requested": self.pause_event.is_set(),
                "cancel_reason": self.cancel_reason,
                "last_error": self.last_error,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "worker_alive": bool(self.worker and self.worker.is_alive()),
            }

    def shutdown(self) -> None:
        self.cancel("shutdown", wait_timeout_s=max(1.0, self.config.cancel_timeout_s))
        worker = self.worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=max(1.0, self.config.cancel_timeout_s))

    def _run(self) -> None:
        terminal_state = "completed"
        terminal_error = None
        try:
            route = self.current_route
            if route is None:
                raise RuntimeError("Patrol route disappeared before execution")
            index = 0
            while not self.stop_event.is_set():
                point = route["points"][index]
                with self.lock:
                    self.current_index = index
                outcome = self._navigate_to_point(point)
                if outcome == "canceled":
                    terminal_state = "canceled"
                    break
                if outcome == "retry":
                    continue
                if outcome != "succeeded":
                    raise RuntimeError(outcome)
                if not self._dwell(float(point.get("dwell_s", 0.0))):
                    terminal_state = "canceled"
                    break
                index += 1
                if index < len(route["points"]):
                    continue
                if route.get("loop"):
                    index = 0
                    with self.lock:
                        self.cycle += 1
                else:
                    terminal_state = "completed"
                    break
        except Exception as exc:
            terminal_state = "failed"
            terminal_error = str(exc)
        finally:
            with self.lock:
                cancel_reason = self.cancel_reason
                if self.stop_event.is_set() and terminal_state == "completed":
                    terminal_state = "canceled"
                self.state = terminal_state
                self.last_error = terminal_error
                self.current_goal_id = None
                self.completed_at = time.time()
            if cancel_reason != "manual_override":
                self._safe_stop_motion()

    def _navigate_to_point(self, point: dict) -> str:
        if not self._wait_while_paused():
            return "canceled"
        gate = self.before_goal()
        if isinstance(gate, dict) and not gate.get("ok", False):
            return gate.get("message") or "Command arbiter rejected patrol navigation"
        response = self.navigation.send_goal(point["x"], point["y"], point["yaw"])
        goal_id = response["goal_id"]
        with self.lock:
            self.current_goal_id = goal_id
        deadline = time.monotonic() + self.config.goal_timeout_s
        while True:
            if self.stop_event.is_set():
                self.navigation.cancel_goal(wait_timeout_s=self.config.cancel_timeout_s)
                return "canceled"
            if self.pause_event.is_set():
                self.navigation.cancel_goal(wait_timeout_s=self.config.cancel_timeout_s)
                if not self._wait_while_paused():
                    return "canceled"
                return "retry"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.navigation.cancel_goal(wait_timeout_s=self.config.cancel_timeout_s)
                return f"Patrol point '{point['name']}' exceeded {self.config.goal_timeout_s:.1f}s timeout"
            result = self.navigation.wait_for_goal(
                goal_id,
                timeout_s=min(self.config.poll_period_s, remaining),
            )
            state = result.get("state")
            if state == "timeout":
                continue
            with self.lock:
                self.current_goal_id = None
            if state == "succeeded":
                return "succeeded"
            if state == "canceled" and self.pause_event.is_set():
                if not self._wait_while_paused():
                    return "canceled"
                return "retry"
            return f"Patrol point '{point['name']}' navigation ended with state={state}: {result.get('error')}"

    def _wait_while_paused(self) -> bool:
        if not self.pause_event.is_set():
            return not self.stop_event.is_set()
        self._safe_stop_motion()
        with self.lock:
            self.state = "paused"
        while self.pause_event.is_set() and not self.stop_event.wait(self.config.poll_period_s):
            pass
        if self.stop_event.is_set():
            return False
        with self.lock:
            self.state = "running"
        return True

    def _dwell(self, duration_s: float) -> bool:
        remaining = max(0.0, duration_s)
        while remaining > 0:
            if self.stop_event.is_set():
                return False
            if self.pause_event.is_set():
                if not self._wait_while_paused():
                    return False
                continue
            started = time.monotonic()
            self.stop_event.wait(min(self.config.poll_period_s, remaining))
            remaining -= time.monotonic() - started
        return not self.stop_event.is_set()

    def _set_routes(self, raw_routes: dict) -> None:
        if not isinstance(raw_routes, dict):
            raise ValueError("Patrol routes must be a mapping keyed by route name")
        normalized = {
            str(name): self._normalize_route(str(name), value)
            for name, value in raw_routes.items()
        }
        with self.lock:
            self.routes = normalized
            self.routes_error = None

    def _normalize_route(self, name: str, raw: dict) -> dict:
        if not isinstance(raw, dict):
            raise ValueError(f"Patrol route '{name}' must be an object")
        raw_points = raw.get("points")
        if not isinstance(raw_points, list) or not raw_points:
            raise ValueError(f"Patrol route '{name}' must contain at least one point")
        map_name = raw.get("map_name")
        return {
            "name": name,
            "map_name": MappingService._safe_name(str(map_name)) if map_name else None,
            "loop": bool(raw.get("loop", False)),
            "points": [
                self._normalize_point(point, index)
                for index, point in enumerate(raw_points)
            ],
        }

    @staticmethod
    def _normalize_point(raw: dict, index: int) -> dict:
        if not isinstance(raw, dict):
            raise ValueError(f"Patrol point {index + 1} must be an object")
        try:
            x = float(raw["x"])
            y = float(raw["y"])
            dwell_s = float(raw.get("dwell_s", 0.0))
            if "yaw_deg" in raw:
                yaw = math.radians(float(raw["yaw_deg"]))
            else:
                yaw = float(raw.get("yaw", 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid patrol point {index + 1}: {exc}")
        if not all(math.isfinite(value) for value in (x, y, yaw, dwell_s)):
            raise ValueError(f"Patrol point {index + 1} contains non-finite values")
        if dwell_s < 0 or dwell_s > 3600:
            raise ValueError(f"Patrol point {index + 1} dwell_s must be between 0 and 3600")
        yaw = (yaw + math.pi) % (2 * math.pi) - math.pi
        return {
            "name": str(raw.get("name") or f"point_{index + 1}"),
            "x": x,
            "y": y,
            "yaw": yaw,
            "yaw_deg": math.degrees(yaw),
            "dwell_s": dwell_s,
        }

    @staticmethod
    def _copy_route(route: Optional[dict]) -> Optional[dict]:
        if route is None:
            return None
        return {
            "name": route["name"],
            "map_name": route.get("map_name"),
            "loop": bool(route.get("loop", False)),
            "points": [dict(point) for point in route.get("points", [])],
        }

    def _safe_stop_motion(self) -> None:
        try:
            self.stop_motion()
        except Exception:
            pass
