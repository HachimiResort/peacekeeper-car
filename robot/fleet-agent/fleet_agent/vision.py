"""Model-agnostic detection data and preloaded YOLO TensorRT inference.

This module never opens a camera and never imports control modules. The agent
will later pass it frames from its already-owned ``VideoService`` instance.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import VisionConfig


class VisionUnavailable(RuntimeError):
    """Raised when edge inference was requested but cannot be started."""


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    xyxy: Tuple[float, float, float, float]

    def as_dict(self) -> Dict[str, Any]:
        x1, y1, x2, y2 = self.xyxy
        return {
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "bbox": {
                "x": round(x1, 1),
                "y": round(y1, 1),
                "width": round(max(0.0, x2 - x1), 1),
                "height": round(max(0.0, y2 - y1), 1),
            },
        }


@dataclass(frozen=True)
class VisionResult:
    model: str
    image_width: int
    image_height: int
    inference_ms: float
    detections: Tuple[Detection, ...]
    target_labels: Tuple[str, ...]

    def as_dict(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for detection in self.detections:
            counts[detection.label] = counts.get(detection.label, 0) + 1
        triggers = {
            label: {"detected": counts.get(label, 0) > 0, "count": counts.get(label, 0)}
            for label in self.target_labels
        }
        return {
            "ok": True,
            "model": self.model,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "inference_ms": round(self.inference_ms, 1),
            "detections": [item.as_dict() for item in self.detections],
            "label_counts": counts,
            "triggers": triggers,
        }


class VisionService:
    """Runs a configured TensorRT model only when a frame is supplied."""

    def __init__(self, config: VisionConfig, model_factory: Optional[Callable[[str], Any]] = None):
        self.config = config
        self._model_factory = model_factory or self._default_model_factory
        self._model: Optional[Any] = None
        self._last_result: Optional[VisionResult] = None
        self._last_error: Optional[str] = None

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "engine_path": self.config.engine_path or None,
            "model_loaded": self._model is not None,
            "target_labels": list(self.config.target_labels),
            "last_result": self._last_result.as_dict() if self._last_result else None,
            "last_error": self._last_error,
        }

    def load(self) -> None:
        """Load and validate the configured model before serving requests."""
        self._load_model()

    def analyze(self, frame: Any) -> Tuple[VisionResult, Any]:
        model = self._load_model()
        try:
            raw_result = model(
                frame,
                device=0,
                imgsz=self.config.imgsz,
                conf=self.config.confidence,
                verbose=False,
            )[0]
            result = self._to_result(raw_result, frame)
            annotated_frame = raw_result.plot()
        except Exception as exc:
            self._last_error = str(exc)
            raise VisionUnavailable(f"YOLO inference failed: {exc}") from exc
        self._last_result = result
        self._last_error = None
        return result, annotated_frame

    def _load_model(self) -> Any:
        if not self.config.enabled:
            raise VisionUnavailable("Vision is disabled in the fleet-agent configuration")
        if not self.config.engine_path:
            raise VisionUnavailable("Vision is enabled but no TensorRT engine_path is configured")
        if self._model is None:
            try:
                self._model = self._model_factory(self.config.engine_path)
            except Exception as exc:
                self._last_error = str(exc)
                raise VisionUnavailable(f"Cannot load TensorRT engine: {exc}") from exc
        return self._model

    def _to_result(self, raw_result: Any, frame: Any) -> VisionResult:
        names = raw_result.names
        detections: List[Detection] = []
        for box in raw_result.boxes:
            class_id = int(box.cls[0])
            label = names[class_id] if isinstance(names, dict) else names[class_id]
            xyxy = tuple(float(value) for value in box.xyxy[0].tolist())
            detections.append(
                Detection(
                    label=str(label),
                    confidence=float(box.conf[0]),
                    xyxy=(xyxy[0], xyxy[1], xyxy[2], xyxy[3]),
                )
            )
        height, width = frame.shape[:2]
        speed = getattr(raw_result, "speed", {}) or {}
        inference_ms = float(speed.get("inference", 0.0))
        return VisionResult(
            model=Path(self.config.engine_path).name,
            image_width=int(width),
            image_height=int(height),
            inference_ms=inference_ms,
            detections=tuple(detections),
            target_labels=tuple(str(value) for value in self.config.target_labels),
        )

    @staticmethod
    def _default_model_factory(engine_path: str) -> Any:
        # TensorRT 8.5's Python bindings still use np.bool on the Jetson image.
        import numpy as np

        if not hasattr(np, "bool"):
            np.bool = bool  # type: ignore[attr-defined]
        from ultralytics import YOLO

        return YOLO(engine_path, task="detect")


class VisionWorkerClient:
    """Loopback client for a host-level TensorRT vision worker."""

    def __init__(self, config: VisionConfig):
        if not config.worker_url:
            raise ValueError("Vision worker_url must be configured")
        parsed = urlparse(config.worker_url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError("Vision worker_url must use an HTTP loopback address")
        self.config = config
        self.base_url = config.worker_url.rstrip("/")
        self._last_error: Optional[str] = None

    def infer(self, jpeg: bytes) -> Tuple[Dict[str, Any], bytes]:
        request = Request(
            f"{self.base_url}/infer",
            data=jpeg,
            method="POST",
            headers={"Content-Type": "image/jpeg", "Content-Length": str(len(jpeg))},
        )
        try:
            with urlopen(request, timeout=self.config.worker_timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            message = self._response_message(exc.read(), str(exc))
            self._last_error = message
            raise VisionUnavailable(f"Vision worker rejected inference: {message}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            self._last_error = str(exc)
            raise VisionUnavailable(f"Vision worker is unavailable: {exc}") from exc
        if not payload.get("ok", False):
            message = str(payload.get("message") or "Vision worker returned an invalid response")
            self._last_error = message
            raise VisionUnavailable(message)
        try:
            image = base64.b64decode(payload.pop("annotated_jpeg_base64"), validate=True)
        except (KeyError, ValueError) as exc:
            self._last_error = str(exc)
            raise VisionUnavailable("Vision worker response has no valid annotated image") from exc
        self._last_error = None
        return payload, image

    def status(self) -> Dict[str, Any]:
        status: Dict[str, Any] = {
            "enabled": self.config.enabled,
            "engine_path": self.config.engine_path or None,
            "model_loaded": False,
            "target_labels": list(self.config.target_labels),
            "worker_url": self.base_url,
            "last_result": None,
            "last_error": self._last_error,
        }
        try:
            request = Request(f"{self.base_url}/health", method="GET")
            with urlopen(request, timeout=min(self.config.worker_timeout_s, 2.0)) as response:
                health = json.loads(response.read().decode("utf-8"))
            if health.get("ok", False):
                status["model_loaded"] = bool(health.get("model_loaded", False))
                if health.get("last_error"):
                    status["last_error"] = str(health["last_error"])
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            if status["last_error"] is None:
                status["last_error"] = f"Vision worker is unavailable: {exc}"
        return status

    @staticmethod
    def _response_message(body: bytes, fallback: str) -> str:
        try:
            return str(json.loads(body.decode("utf-8")).get("message") or fallback)
        except Exception:
            return fallback


class VisionCaptureService:
    """Coordinates one existing camera frame with one vision inference.

    ``video`` is intentionally duck-typed so this service does not own the
    camera and remains easy to test. It requires ``read_frame`` and
    ``encode_jpeg`` methods supplied by ``VideoService``.
    """

    def __init__(self, video: Any, vision: VisionService, worker: Optional[VisionWorkerClient] = None):
        self.video = video
        self.vision = vision
        self.worker = worker
        self._lock = threading.RLock()
        self._latest_payload: Optional[Dict[str, Any]] = None
        self._latest_jpeg: Optional[bytes] = None

    def capture(self) -> Dict[str, Any]:
        with self._lock:
            frame = self.video.read_frame()
            if frame is None:
                raise VisionUnavailable("Camera frame is unavailable")
            if self.worker is not None:
                source_jpeg = self.video.encode_jpeg(frame)
                if source_jpeg is None:
                    raise VisionUnavailable("Could not encode camera frame for vision worker")
                payload, jpeg = self.worker.infer(source_jpeg)
            else:
                result, annotated_frame = self.vision.analyze(frame)
                jpeg = self.video.encode_jpeg(annotated_frame)
                if jpeg is None:
                    raise VisionUnavailable("Could not encode annotated detection image")
                payload = result.as_dict()
                payload["captured_at"] = time.time()
                payload["annotated_image_available"] = True
            self._latest_payload = payload
            self._latest_jpeg = jpeg
            return dict(payload)

    def latest(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._latest_payload) if self._latest_payload else None

    def latest_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    def status(self) -> Dict[str, Any]:
        status = self.worker.status() if self.worker is not None else self.vision.status()
        status["last_result"] = dict(self._latest_payload) if self._latest_payload else None
        status["latest_available"] = self._latest_payload is not None
        return status
