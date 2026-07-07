"""OpenCV video streaming, snapshots, and recording."""
from __future__ import annotations

import base64
import threading
import time
from pathlib import Path
from typing import Optional

from .config import VideoConfig

try:
    import cv2
except Exception:  # pragma: no cover - allows non-camera development.
    cv2 = None


FALLBACK_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/"
    "9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/ASP/xAAUEQEAA"
    "AAAAAAAAAAAAAAAAAAA/9oACAECAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Ap//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAA"
    "E/IV//2gAMAwEAAgADAAAAEP/EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EFB"
    "ABAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)


class VideoService:
    def __init__(self, config: VideoConfig, run_dir: Path):
        self.config = config
        self.run_dir = run_dir
        self.snapshots_dir = run_dir / "snapshots"
        self.recordings_dir = run_dir / "recordings"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.capture = None
        self.last_jpeg = FALLBACK_JPEG
        self.recording = False
        self.record_writer = None
        self.recording_path: Optional[Path] = None
        self.streaming = False

    def start(self) -> None:
        if cv2 is None:
            self.streaming = False
            return
        with self.lock:
            if self.capture is not None:
                return
            capture = cv2.VideoCapture(self.config.device)
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
            capture.set(cv2.CAP_PROP_FPS, self.config.fps)
            if capture.isOpened():
                self.capture = capture
                self.streaming = True
            else:
                capture.release()
                self.capture = None
                self.streaming = False

    def stop(self) -> None:
        with self.lock:
            self.stop_recording()
            if self.capture is not None:
                self.capture.release()
            self.capture = None
            self.streaming = False

    def read_jpeg(self) -> bytes:
        self.start()
        with self.lock:
            if cv2 is None or self.capture is None:
                return self.last_jpeg
            ok, frame = self.capture.read()
            if not ok or frame is None:
                return self.last_jpeg
            ok, encoded = cv2.imencode(".jpg", frame)
            if not ok:
                return self.last_jpeg
            self.last_jpeg = encoded.tobytes()
            if self.recording and self.record_writer is not None:
                self.record_writer.write(frame)
            return self.last_jpeg

    def mjpeg_frames(self):
        while True:
            frame = self.read_jpeg()
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(max(1 / max(self.config.fps, 1), 0.03))

    def snapshot(self) -> dict:
        frame = self.read_jpeg()
        path = self.snapshots_dir / f"snapshot-{time.strftime('%H%M%S')}.jpg"
        path.write_bytes(frame)
        return {"ok": True, "path": str(path)}

    def start_recording(self) -> dict:
        if cv2 is None:
            return {"ok": False, "message": "OpenCV is not available"}
        self.start()
        with self.lock:
            if self.recording:
                return {"ok": True, "path": str(self.recording_path)}
            path = self.recordings_dir / f"recording-{time.strftime('%H%M%S')}.mp4"
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(
                str(path),
                fourcc,
                max(self.config.fps, 1),
                (self.config.width, self.config.height),
            )
            if not writer.isOpened():
                return {"ok": False, "message": "Could not open video writer"}
            self.record_writer = writer
            self.recording_path = path
            self.recording = True
            return {"ok": True, "path": str(path)}

    def stop_recording(self) -> dict:
        with self.lock:
            path = self.recording_path
            if self.record_writer is not None:
                self.record_writer.release()
            self.record_writer = None
            self.recording = False
            self.recording_path = None
            return {"ok": True, "path": str(path) if path else None}

    def status(self) -> dict:
        return {
            "streaming": self.streaming,
            "recording": self.recording,
            "recording_path": str(self.recording_path) if self.recording_path else None,
            "device": self.config.device,
        }
