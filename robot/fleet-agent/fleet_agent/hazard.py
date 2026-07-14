"""Vehicle-side hazard supervision, evidence capture, and reliable event delivery."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

from .config import HazardConfig
from .state import Mode, RuntimeState


class EvidenceStore:
    def __init__(self, data_dir: Path, max_bytes: int):
        self.root = data_dir / "evidence"
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def save(self, event_key: str, raw_jpeg: Optional[bytes], annotated_jpeg: Optional[bytes], metadata: dict) -> dict:
        directory = self.root / event_key
        directory.mkdir(parents=True, exist_ok=True)
        artifacts = {}
        for kind, body in (("raw", raw_jpeg), ("annotated", annotated_jpeg)):
            if body:
                path = directory / f"{kind}.jpg"
                path.write_bytes(body)
                artifacts[kind] = {
                    "path": str(path),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body),
                    "content_type": "image/jpeg",
                }
        metadata_path = directory / "metadata.json"
        metadata_body = json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        metadata_path.write_bytes(metadata_body)
        artifacts["metadata"] = {
            "path": str(metadata_path),
            "sha256": hashlib.sha256(metadata_body).hexdigest(),
            "size_bytes": len(metadata_body),
            "content_type": "application/json",
        }
        self._trim_uploaded()
        return artifacts

    def _trim_uploaded(self) -> None:
        files = [path for path in self.root.glob("*/*") if path.is_file() and path.name != ".uploaded" and (path.parent / ".uploaded").exists()]
        total = sum(path.stat().st_size for path in self.root.glob("*/*") if path.is_file())
        for path in sorted(files, key=lambda item: item.stat().st_mtime):
            if total <= self.max_bytes:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size


class EventOutbox:
    def __init__(self, data_dir: Path, config: HazardConfig, token: str):
        self.root = data_dir / "outbox"
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.token = config.mission_api_token or token
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_error: Optional[str] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="peacekeeper-hazard-outbox", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def enqueue(self, event: dict, artifacts: dict) -> None:
        path = self.root / f"{event['event_key']}.json"
        path.write_text(json.dumps({"event": event, "artifacts": artifacts}, ensure_ascii=False), encoding="utf-8")
        self._wake.set()

    def status(self) -> dict:
        return {
            "pending": len(list(self.root.glob("*.json"))),
            "mission_api_configured": bool(self.config.mission_api_url),
            "last_error": self.last_error,
            "worker_alive": bool(self._thread and self._thread.is_alive()),
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.config.mission_api_url:
                for path in sorted(self.root.glob("*.json")):
                    if self._stop.is_set():
                        return
                    try:
                        self._deliver(json.loads(path.read_text(encoding="utf-8")))
                        path.unlink(missing_ok=True)
                        self.last_error = None
                    except Exception as exc:
                        self.last_error = str(exc)
                        break
            self._wake.wait(5.0)
            self._wake.clear()

    def _deliver(self, record: dict) -> None:
        base = self.config.mission_api_url.rstrip("/")
        event = record["event"]
        self._request_json(f"{base}/internal/robot-events", event)
        for kind, artifact in record.get("artifacts", {}).items():
            body = Path(artifact["path"]).read_bytes()
            boundary = f"peacekeeper-{uuid.uuid4().hex}"
            multipart = (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\n{kind}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{kind}\"\r\n"
                f"Content-Type: {artifact['content_type']}\r\n\r\n"
            ).encode() + body + f"\r\n--{boundary}--\r\n".encode()
            request = Request(
                f"{base}/internal/robot-events/{event['event_key']}/evidence",
                data=multipart,
                method="POST",
                headers={"X-Peacekeeper-Token": self.token, "Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            with urlopen(request, timeout=20.0) as response:
                response.read()
        evidence_dir = Path(next(iter(record.get("artifacts", {}).values()), {}).get("path", "")).parent
        if evidence_dir.is_dir():
            (evidence_dir / ".uploaded").touch()

    def _request_json(self, url: str, payload: dict) -> dict:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"X-Peacekeeper-Token": self.token, "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=10.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Mission API event delivery failed: {exc}") from exc


class HazardSupervisor:
    def __init__(
        self,
        config: HazardConfig,
        state: RuntimeState,
        patrol,
        navigation,
        command_arbiter,
        vision_capture,
        audio,
        evidence: EvidenceStore,
        outbox: EventOutbox,
        health_provider: Callable[[], dict],
    ):
        self.config = config
        self.runtime_state = state
        self.patrol = patrol
        self.navigation = navigation
        self.command_arbiter = command_arbiter
        self.vision_capture = vision_capture
        self.audio = audio
        self.evidence = evidence
        self.outbox = outbox
        self.health_provider = health_provider
        self.lock = threading.RLock()
        self.enabled = False
        self.state = "DISABLED"
        self.history: deque[bool] = deque(maxlen=max(1, config.confirmation_window))
        self.current_event_key: Optional[str] = None
        self.current_detection: Optional[dict] = None
        self.last_target_seen_at: Optional[float] = None
        self.hold_started_at: Optional[float] = None
        self.last_event_at: Optional[float] = None
        self.takeover = False
        self.hold_requested = False
        self.last_error: Optional[str] = None
        self.alarm_audio_error: Optional[str] = None
        self._alarm_event_key: Optional[str] = None

    def set_enabled(self, enabled: bool) -> dict:
        with self.lock:
            if not enabled and self.state == "HOLDING":
                raise RuntimeError("Cannot disable monitoring while a hazard hold is active")
            self.enabled = bool(enabled)
            self.state = "MONITORING" if enabled else "DISABLED"
            self.history.clear()
            if not enabled:
                self._stop_alarm_unlocked()
        self.vision_capture.set_monitor_enabled(enabled)
        return self.status()

    def observe(self, payload: dict) -> None:
        with self.lock:
            if not self.enabled:
                return
            targets = [
                item for item in payload.get("detections", [])
                if item.get("label") in self.vision_capture.vision.config.target_labels
                and float(item.get("confidence") or 0.0) >= self.config.confidence
            ]
            now = time.time()
            if targets:
                self.last_target_seen_at = now
                self.current_detection = min(targets, key=lambda item: float(item.get("range_m") or 1e9))
            if self.state == "HOLDING":
                if not targets and self._can_auto_resume_unlocked(now):
                    self._resume_unlocked("target_clear_timeout")
                return
            actionable = [
                item for item in targets
                if item.get("range_m") is not None
                and float(item["range_m"]) <= self.config.stop_distance_m
            ]
            hit = bool(actionable)
            self.history.append(hit)
            hits = sum(self.history)
            if hit:
                self.state = "CONFIRMING" if hits < self.config.confirmation_hits else "STOPPING"
            elif hits:
                self.state = "SUSPECT"
            else:
                self.state = "MONITORING"
            patrol_status = self.patrol.status()
            if (
                hits >= self.config.confirmation_hits
                and len(self.history) >= self.config.confirmation_hits
                and patrol_status.get("state") == "running"
                and (self.last_event_at is None or now - self.last_event_at >= self.config.cooldown_s)
            ):
                self._stop_unlocked(payload, min(actionable, key=lambda item: float(item["range_m"])))

    def take_over(self, event_key: str) -> dict:
        with self.lock:
            self._require_current_event(event_key)
            self.takeover = True
            self._stop_alarm_unlocked()
            self.patrol.cancel("hazard_takeover", wait_timeout_s=1.0)
            self.runtime_state.set_mode(Mode.MANUAL)
            self.state = "TAKEOVER"
            return self.status()

    def set_hold(self, event_key: str, hold: bool = True) -> dict:
        with self.lock:
            self._require_current_event(event_key)
            self.hold_requested = hold
            return self.status()

    def resume(self, event_key: str) -> dict:
        with self.lock:
            self._require_current_event(event_key)
            if self.takeover:
                raise RuntimeError("Operator takeover is active; restart patrol explicitly")
            if self.last_target_seen_at and time.time() - self.last_target_seen_at < self.config.clear_after_s:
                raise RuntimeError("Hazard has not remained clear for the configured interval")
            if not self._healthy_unlocked():
                raise RuntimeError("Vision or range sensors are not healthy")
            self._resume_unlocked("operator_resume")
            return self.status()

    def status(self) -> dict:
        with self.lock:
            remaining = None
            if self.state == "HOLDING" and self.last_target_seen_at is not None:
                remaining = max(0.0, self.config.clear_after_s - (time.time() - self.last_target_seen_at))
            return {
                "enabled": self.enabled,
                "state": self.state,
                "current_event_key": self.current_event_key,
                "current_detection": self.current_detection,
                "confirmation_hits": sum(self.history),
                "confirmation_window": self.config.confirmation_window,
                "hold_started_at": self.hold_started_at,
                "auto_resume_remaining_s": round(remaining, 1) if remaining is not None else None,
                "takeover": self.takeover,
                "hold_requested": self.hold_requested,
                "last_error": self.last_error,
                "alarm_audio": self._alarm_audio_status_unlocked(),
                "outbox": self.outbox.status(),
            }

    def _stop_unlocked(self, payload: dict, detection: dict) -> None:
        event_key = str(uuid.uuid4())
        now = time.time()
        self.current_event_key = event_key
        self.current_detection = dict(detection)
        self.runtime_state.set_mode(Mode.HAZARD_HOLD)
        try:
            self.patrol.pause()
            self.navigation.cancel_goal()
            self.command_arbiter.stop()
            self.runtime_state.set_mode(Mode.HAZARD_HOLD)
        except Exception as exc:
            self.last_error = str(exc)
            self.command_arbiter.stop()
            self.runtime_state.set_mode(Mode.HAZARD_HOLD)
        self.state = "HOLDING"
        self.hold_started_at = now
        self.last_event_at = now
        self.takeover = False
        self.hold_requested = False
        patrol_status = self.patrol.status()
        action_timeline = [{"action": "hazard_stop", "timestamp": now}]
        self._play_alarm_unlocked(event_key, action_timeline)
        metadata = {
            "event_key": event_key,
            "robot_id": self.config.robot_id,
            "occurred_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "model": payload.get("model"),
            "observation": payload,
            "trigger_detection": detection,
            "patrol": patrol_status,
            "action_timeline": action_timeline,
        }
        artifacts = self.evidence.save(
            event_key,
            self.vision_capture.latest_source_jpeg(),
            self.vision_capture.latest_jpeg(),
            metadata,
        )
        self.outbox.enqueue(
            {
                "event_key": event_key,
                "robot_id": self.config.robot_id,
                "mission_id": None,
                "event_type": "hazard_detected",
                "severity": "critical",
                "payload": metadata,
                "occurred_at": metadata["occurred_at"],
                "create_alert": True,
            },
            artifacts,
        )

    def _can_auto_resume_unlocked(self, now: float) -> bool:
        return bool(
            not self.takeover
            and not self.hold_requested
            and self.last_target_seen_at is not None
            and now - self.last_target_seen_at >= self.config.clear_after_s
            and self._healthy_unlocked()
        )

    def _healthy_unlocked(self) -> bool:
        status = self.health_provider()
        range_status = status.get("range_estimation") or {}
        return bool(
            status.get("model_loaded")
            and not status.get("last_error")
            and (range_status.get("depth_available") or range_status.get("scan_available"))
        )

    def _resume_unlocked(self, reason: str) -> None:
        patrol_status = self.patrol.status()
        if patrol_status.get("state") != "paused":
            raise RuntimeError(f"Cannot resume hazard hold while patrol state={patrol_status.get('state')}")
        self.state = "AUTO_RESUMING" if reason == "target_clear_timeout" else "RESUMING"
        self._stop_alarm_unlocked()
        self.patrol.resume()
        self.runtime_state.set_mode(Mode.NAV_PATROL)
        self.state = "MONITORING"
        self.current_event_key = None
        self.current_detection = None
        self.hold_started_at = None
        self.history.clear()

    def _play_alarm_unlocked(self, event_key: str, timeline: list[dict]) -> None:
        asset = str(self.config.alarm_audio_asset or "").strip()
        timestamp = time.time()
        self.alarm_audio_error = None
        self._alarm_event_key = None
        if not asset:
            timeline.append({
                "action": "hazard_alarm_skipped",
                "timestamp": timestamp,
                "reason": "alarm audio is not configured",
            })
            return
        try:
            self.audio.play(
                asset,
                loop=bool(self.config.alarm_audio_loop),
                volume=int(self.config.alarm_audio_volume),
            )
            self._alarm_event_key = event_key
            timeline.append({
                "action": "hazard_alarm_started",
                "timestamp": timestamp,
                "asset": asset,
                "loop": bool(self.config.alarm_audio_loop),
                "volume": int(self.config.alarm_audio_volume),
            })
        except Exception as exc:
            self.alarm_audio_error = str(exc)
            timeline.append({
                "action": "hazard_alarm_failed",
                "timestamp": timestamp,
                "asset": asset,
                "error": self.alarm_audio_error,
            })

    def _stop_alarm_unlocked(self) -> None:
        if self._alarm_event_key is None:
            return
        try:
            audio_status = self.audio.status()
            configured_asset = str(self.config.alarm_audio_asset or "").strip()
            if not audio_status.get("playing") or audio_status.get("asset") == configured_asset:
                self.audio.stop()
        except Exception as exc:
            self.alarm_audio_error = str(exc)
            return
        self._alarm_event_key = None

    def _alarm_audio_status_unlocked(self) -> dict:
        asset = str(self.config.alarm_audio_asset or "").strip()
        configured = bool(asset)
        service_status: dict[str, Any] = {}
        asset_names: set[str] = set()
        service_error: Optional[str] = None
        try:
            service_status = self.audio.status()
            asset_names = {str(item.get("name")) for item in self.audio.assets().get("assets", [])}
        except Exception as exc:
            service_error = str(exc)

        try:
            volume = int(self.config.alarm_audio_volume)
            volume_valid = 0 <= volume <= 100
        except (TypeError, ValueError):
            volume = 0
            volume_valid = False

        ready = bool(
            configured
            and volume_valid
            and service_status.get("available")
            and asset in asset_names
        )
        last_error = self.alarm_audio_error or service_status.get("last_error") or service_error
        if configured and last_error is None:
            if not volume_valid:
                last_error = "Alarm audio volume must be an integer from 0 to 100"
            elif not service_status.get("available"):
                last_error = "Audio playback is disabled or ffplay is not available"
            elif asset not in asset_names:
                last_error = f"Audio asset '{asset}' was not found"

        playing = bool(
            self._alarm_event_key
            and service_status.get("playing")
            and service_status.get("asset") == asset
        )
        return {
            "configured": configured,
            "ready": ready,
            "playing": playing,
            "asset": asset or None,
            "loop": bool(self.config.alarm_audio_loop),
            "volume": volume,
            "last_error": last_error,
        }

    def _require_current_event(self, event_key: str) -> None:
        if self.current_event_key != event_key or self.state not in {"HOLDING", "TAKEOVER"}:
            raise RuntimeError("Hazard event is not the active vehicle hold")
