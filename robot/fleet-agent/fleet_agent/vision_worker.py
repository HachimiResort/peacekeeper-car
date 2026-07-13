"""Jetson-local TensorRT vision worker.

Run this module on the Jetson host, where the NVIDIA Python packages are
available. It binds only to loopback, never opens a camera, and accepts JPEG
frames from the fleet-agent's already-owned VideoService.
"""
from __future__ import annotations

import argparse
import base64
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from typing import Any, Dict

from .config import VisionConfig
from .vision import VisionService, VisionUnavailable


MAX_IMAGE_BYTES = 8 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the loopback-only Jetson YOLO TensorRT worker.")
    parser.add_argument("--engine", required=True, help="Absolute path to the Jetson-built TensorRT engine.")
    parser.add_argument("--host", default="127.0.0.1", help="Loopback host to listen on.")
    parser.add_argument("--port", type=int, default=8092, help="Loopback port to listen on.")
    parser.add_argument("--confidence", type=float, default=0.4)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--target-label", action="append", default=["cat"], help="Label reported in trigger summaries; repeatable.")
    return parser.parse_args()


def create_server(config: VisionConfig, host: str = "127.0.0.1", port: int = 8092) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("vision worker must bind to loopback only")
    vision = VisionService(config)
    vision.load()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            print("vision-worker: " + format % args)

        def _json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path != "/health":
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "Not found"})
                return
            self._json(HTTPStatus.OK, {"ok": True, **vision.status()})

        def do_POST(self) -> None:
            if self.path != "/infer":
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "Not found"})
                return
            content_length = self.headers.get("Content-Length")
            if content_length is None:
                self._json(HTTPStatus.LENGTH_REQUIRED, {"ok": False, "message": "Content-Length is required"})
                return
            try:
                length = int(content_length)
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "Invalid Content-Length"})
                return
            if length <= 0 or length > MAX_IMAGE_BYTES:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "message": "JPEG payload exceeds limit"})
                return
            try:
                import cv2
                import numpy as np

                raw = self.rfile.read(length)
                frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    raise VisionUnavailable("JPEG payload could not be decoded")
                result, annotated = vision.analyze(frame)
                ok, encoded = cv2.imencode(".jpg", annotated)
                if not ok:
                    raise VisionUnavailable("Annotated image could not be encoded")
                payload = result.as_dict()
                payload["captured_at"] = time.time()
                payload["annotated_image_available"] = True
                payload["annotated_jpeg_base64"] = base64.b64encode(encoded.tobytes()).decode("ascii")
                self._json(HTTPStatus.OK, payload)
            except VisionUnavailable as exc:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "message": str(exc)})
            except Exception as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    args = parse_args()
    config = VisionConfig(
        enabled=True,
        engine_path=args.engine,
        confidence=args.confidence,
        imgsz=args.imgsz,
        target_labels=args.target_label,
    )
    server = create_server(config, args.host, args.port)
    print(f"vision-worker listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
