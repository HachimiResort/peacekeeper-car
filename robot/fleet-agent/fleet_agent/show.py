"""Vehicle-local, open-loop music show executor."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone


LIGHTS = {
    "off": (False, False), "left": (True, False), "right": (False, True), "both": (True, True),
    "left_blink": (True, False), "right_blink": (False, True), "both_blink": (True, True), "alternate": (True, False),
}


class ShowService:
    def __init__(self, arbiter, controller, state):
        self.arbiter, self.controller, self.state = arbiter, controller, state
        self.lock = threading.RLock(); self.plan = None; self.status_value = {"state": "idle"}; self.thread = None; self.abort_event = threading.Event()

    def prepare(self, payload: dict) -> dict:
        events = payload.get("events", [])
        if self.state.estop or self.state.mode.value != "IDLE": raise ValueError(f"show requires IDLE; current mode={self.state.mode.value}")
        if not events: raise ValueError("show has no events")
        for event in events:
            if event.get("channel") not in {"motion", "light"}: raise ValueError("invalid show channel")
            has_utc = isinstance(event.get("execute_at_utc"), str)
            has_offset = isinstance(event.get("offset_ms"), (int, float)) and event["offset_ms"] >= 0
            if not has_utc and not has_offset: raise ValueError("event execute_at_utc or non-negative offset_ms is required")
        with self.lock:
            self.abort(); self.plan = {"show_id": payload.get("show_id"), "events": sorted(events, key=_event_order)}
            self.state.set_mode(type(self.state.mode).SHOW)
            self.status_value = {"state": "prepared", "show_id": self.plan["show_id"], "event_count": len(events), "error": None}
        return self.status()

    def commit(self, start_at_utc: str) -> dict:
        with self.lock:
            if not self.plan: raise ValueError("no prepared show")
            start = _parse(start_at_utc); delay = start - time.time()
            if delay < .25: raise ValueError(f"show start is too late (local delay={delay:.3f}s)")
            self.plan["events"] = [{**event, "execute_at_utc": datetime.fromtimestamp(start + float(event["offset_ms"]) / 1000, timezone.utc).isoformat()} if "offset_ms" in event else event for event in self.plan["events"]]
            self.abort_event.clear(); self.status_value.update(state="countdown", start_at_utc=start_at_utc)
            self.thread = threading.Thread(target=self._run, args=(time.monotonic() + delay,), daemon=True); self.thread.start()
        return self.status()

    def abort(self) -> dict:
        self.abort_event.set()
        try: self.arbiter.stop(); self.controller.control_lights(False, False, 0)
        except Exception: pass
        if self.status_value.get("state") not in {"idle", "completed", "failed"}: self.status_value["state"] = "aborted"
        return self.status()

    def status(self) -> dict:
        with self.lock: return {**self.status_value, "now_utc": datetime.now(timezone.utc).isoformat()}

    def _run(self, deadline: float) -> None:
        while not self.abort_event.is_set() and time.monotonic() < deadline: time.sleep(0.01)
        if self.abort_event.is_set(): return
        self.state.set_mode(type(self.state.mode).SHOW)
        with self.lock: self.status_value["state"] = "running"
        try:
            active_motion = None; next_keepalive = 0.0
            for index, event in enumerate(self.plan["events"]):
                target = deadline + (_parse(event["execute_at_utc"]) - _parse(self.status_value["start_at_utc"]))
                while not self.abort_event.is_set() and time.monotonic() < target:
                    if active_motion and time.monotonic() >= next_keepalive:
                        self.arbiter.handle_show_command(**active_motion, ttl_ms=500); next_keepalive = time.monotonic() + .1
                    time.sleep(0.005)
                if self.abort_event.is_set(): return
                if event["channel"] == "motion":
                    active_motion = event["payload"]
                    self.arbiter.handle_show_command(**active_motion, ttl_ms=500); next_keepalive = time.monotonic() + .1
                else: self._light(event["payload"])
                with self.lock: self.status_value.update(current_event=index, next_event=index + 1)
            self.arbiter.stop(); self.controller.control_lights(False, False, 0)
            with self.lock: self.status_value["state"] = "completed"
        except Exception as exc:
            self.arbiter.stop(); self.controller.control_lights(False, False, 0); self.state.set_error(str(exc))
            with self.lock: self.status_value.update(state="failed", error=str(exc))

    def _light(self, payload: dict) -> None:
        effect = payload.get("effect", "off"); left, right = LIGHTS[effect]
        if effect.endswith("blink") or effect == "alternate":
            phase = int(time.monotonic() * 4) % 2 == 0
            left, right = (left and phase, right and phase) if effect != "alternate" else (phase, not phase)
        self.controller.control_lights(left, right, 0)


def _parse(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _event_order(event: dict):
    return float(event["offset_ms"]) if "offset_ms" in event else _parse(event["execute_at_utc"])
