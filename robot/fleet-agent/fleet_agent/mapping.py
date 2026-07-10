"""Map save helpers."""
from __future__ import annotations

import binascii
import re
import struct
import time
import zlib
from pathlib import Path
from typing import List, Optional, Tuple

from .config import AgentConfig
from .process_manager import ProcessManager


class MappingService:
    def __init__(self, config: AgentConfig, process_manager: ProcessManager):
        self.config = config
        self.process_manager = process_manager
        self.maps_dir = Path(config.data_dir) / "maps"
        self.maps_dir.mkdir(parents=True, exist_ok=True)

    def save_map(self, name: str) -> dict:
        safe_name = self._safe_name(name)
        map_path = self.maps_dir / safe_name
        command = self.config.mapping.saver_command.format(map_path=str(map_path))
        attempts = 1
        result = self.process_manager.run_ros_command(command, timeout_s=240.0)
        if result.returncode != 0 and self._looks_like_timeout(result):
            attempts += 1
            time.sleep(1.0)
            result = self.process_manager.run_ros_command(command, timeout_s=240.0)
        yaml_path = map_path.with_suffix(".yaml")
        pgm_path = map_path.with_suffix(".pgm")
        ok = result.returncode == 0 and yaml_path.exists()
        return {
            "ok": ok,
            "name": safe_name,
            "yaml": str(yaml_path),
            "pgm": str(pgm_path),
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
            "attempts": attempts,
        }

    def latest_map(self, name: Optional[str] = None) -> dict:
        if name:
            safe_name = self._safe_name(name)
            yaml_path = self.maps_dir / f"{safe_name}.yaml"
            pgm_path = self.maps_dir / f"{safe_name}.pgm"
            if not yaml_path.exists() and not pgm_path.exists():
                raise FileNotFoundError(f"Map '{safe_name}' was not found")
            return {
                "name": safe_name,
                "yaml": str(yaml_path),
                "pgm": str(pgm_path),
                "exists": yaml_path.exists() or pgm_path.exists(),
            }

        pgm_files = sorted(self.maps_dir.glob("*.pgm"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not pgm_files:
            raise FileNotFoundError("No saved map preview is available yet")
        pgm_path = pgm_files[0]
        return {
            "name": pgm_path.stem,
            "yaml": str(pgm_path.with_suffix(".yaml")),
            "pgm": str(pgm_path),
            "exists": True,
        }

    def render_map_png(self, name: Optional[str] = None) -> bytes:
        info = self.latest_map(name=name)
        width, height, pixels = self._read_pgm(Path(info["pgm"]))
        return self._encode_png(width, height, pixels)

    def export_bundle(self, name: str) -> bytes:
        from .map_bundle import build_map_bundle

        info = self.latest_map(name)
        yaml_path = Path(info["yaml"])
        pgm_path = Path(info["pgm"])
        if not yaml_path.is_file():
            raise FileNotFoundError(f"Map metadata '{yaml_path.name}' was not found")
        if not pgm_path.is_file():
            raise FileNotFoundError(f"Map image '{pgm_path.name}' was not found")
        return build_map_bundle(yaml_path, pgm_path, info["name"])

    def install_bundle(self, payload: bytes, max_bytes: int) -> dict:
        from .map_bundle import install_map_bundle

        return install_map_bundle(payload, self.maps_dir, max_bytes)

    def map_meta(self, name: Optional[str] = None) -> dict:
        info = self.latest_map(name=name)
        yaml_path = Path(info["yaml"])
        pgm_path = Path(info["pgm"])
        if not yaml_path.exists():
            raise FileNotFoundError(f"Map metadata '{yaml_path.name}' was not found")
        if not pgm_path.exists():
            raise FileNotFoundError(f"Map image '{pgm_path.name}' was not found")

        width, height, _ = self._read_pgm(pgm_path)
        yaml_data = self._read_map_yaml(yaml_path)
        origin = yaml_data.get("origin", [0.0, 0.0, 0.0])
        if len(origin) < 3:
            origin = list(origin) + [0.0] * (3 - len(origin))
        resolution = float(yaml_data.get("resolution", 0.05))
        return {
            "name": info["name"],
            "yaml": str(yaml_path),
            "pgm": str(pgm_path),
            "width": width,
            "height": height,
            "resolution": resolution,
            "origin": {
                "x": float(origin[0]),
                "y": float(origin[1]),
                "yaw": float(origin[2]),
            },
            "image": pgm_path.name,
        }

    @staticmethod
    def pixel_to_map(meta: dict, pixel_x: float, pixel_y: float) -> Tuple[float, float]:
        resolution = float(meta["resolution"])
        origin = meta["origin"]
        height = float(meta["height"])
        map_x = float(origin["x"]) + (float(pixel_x) + 0.5) * resolution
        map_y = float(origin["y"]) + (height - float(pixel_y) - 0.5) * resolution
        return map_x, map_y

    @staticmethod
    def _safe_name(name: str) -> str:
        value = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
        value = value.strip("._-")
        return value or "map"

    @staticmethod
    def _read_pgm(path: Path) -> Tuple[int, int, bytes]:
        data = path.read_bytes()
        if not data.startswith((b"P5", b"P2")):
            raise ValueError(f"Unsupported PGM format in {path}")

        offset = 0
        tokens: List[bytes] = []
        while len(tokens) < 4:
            while offset < len(data) and data[offset] in b" \t\r\n":
                offset += 1
            if offset >= len(data):
                break
            if data[offset:offset + 1] == b"#":
                while offset < len(data) and data[offset] not in b"\r\n":
                    offset += 1
                continue
            start = offset
            while offset < len(data) and data[offset] not in b" \t\r\n":
                offset += 1
            tokens.append(data[start:offset])

        if len(tokens) < 4:
            raise ValueError(f"Could not parse PGM header from {path}")

        magic = tokens[0]
        width = int(tokens[1])
        height = int(tokens[2])
        max_value = int(tokens[3])
        if magic == b"P5":
            if offset >= len(data) or data[offset] not in b" \t\r\n":
                raise ValueError(f"PGM header has no payload separator in {path}")
            offset += 2 if data[offset:offset + 2] == b"\r\n" else 1
            pixels = data[offset:offset + width * height]
        else:
            values = [
                int(part)
                for part in re.sub(rb"#.*", b"", data[offset:]).split()
            ]
            if max_value <= 0:
                raise ValueError(f"Invalid PGM max value in {path}")
            pixels = bytes(
                max(0, min(255, int(value * 255 / max_value)))
                for value in values[:width * height]
            )

        if magic == b"P5" and max_value != 255 and max_value > 0:
            pixels = bytes(int(value * 255 / max_value) for value in pixels)
        if len(pixels) != width * height:
            raise ValueError(f"PGM payload size mismatch in {path}")
        return width, height, pixels

    @staticmethod
    def _encode_png(width: int, height: int, pixels: bytes) -> bytes:
        stride = width
        raw = b"".join(
            b"\x00" + pixels[row * stride:(row + 1) * stride]
            for row in range(height)
        )
        compressed = zlib.compress(raw, level=6)

        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                struct.pack("!I", len(payload))
                + kind
                + payload
                + struct.pack("!I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
            )

        return b"".join([
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 0, 0, 0, 0)),
            chunk(b"IDAT", compressed),
            chunk(b"IEND", b""),
        ])

    @staticmethod
    def _read_map_yaml(path: Path) -> dict:
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                return data
        except Exception:
            pass

        data = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "origin":
                numbers = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value)
                data[key] = [float(item) for item in numbers[:3]]
            elif key in {"resolution", "occupied_thresh", "free_thresh"}:
                data[key] = float(value)
            elif key == "negate":
                data[key] = int(value)
            else:
                data[key] = value.strip("'\"")
        return data

    @staticmethod
    def _looks_like_timeout(result) -> bool:
        stderr = str(getattr(result, "stderr", "") or "").lower()
        stdout = str(getattr(result, "stdout", "") or "").lower()
        return "failed to save the map: timeout" in stderr or "failed to save the map: timeout" in stdout
