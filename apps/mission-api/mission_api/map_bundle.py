import binascii
import hashlib
import io
import json
import re
import stat
import struct
import zipfile
import zlib
from dataclasses import dataclass
from typing import Optional

import yaml

EXPECTED_FILES = {"manifest.json", "map.yaml", "map.pgm"}


@dataclass(frozen=True)
class MapBundle:
    logical_name: str
    yaml_bytes: bytes
    image_bytes: bytes
    yaml_sha256: str
    image_sha256: str
    bundle_sha256: str
    resolution: float
    origin: list[float]
    width: int
    height: int
    manifest: dict


def safe_map_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._-")
    if not name:
        raise ValueError("Map name is empty after sanitization")
    return name[:128]


def parse_bundle(payload: bytes, max_bytes: int, logical_name_override: Optional[str] = None) -> MapBundle:
    if len(payload) > max_bytes:
        raise ValueError(f"Compressed map bundle exceeds {max_bytes} bytes")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload), "r")
    except Exception as exc:
        raise ValueError(f"Invalid map ZIP: {exc}") from exc
    with archive:
        infos = archive.infolist()
        names = {item.filename for item in infos}
        if names != EXPECTED_FILES or len(infos) != len(EXPECTED_FILES):
            raise ValueError(f"Map bundle must contain exactly {sorted(EXPECTED_FILES)}")
        total = 0
        for item in infos:
            if item.filename.startswith("/") or ".." in item.filename.split("/"):
                raise ValueError("Map bundle contains an unsafe path")
            mode = item.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise ValueError("Map bundle may not contain symbolic links")
            total += item.file_size
            if total > max_bytes:
                raise ValueError(f"Decompressed map bundle exceeds {max_bytes} bytes")
        manifest_bytes = archive.read("manifest.json")
        yaml_bytes = archive.read("map.yaml")
        image_bytes = archive.read("map.pgm")

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid manifest.json: {exc}") from exc
    if not isinstance(manifest, dict) or int(manifest.get("schema_version", 0)) != 1:
        raise ValueError("Unsupported map bundle schema_version")
    if manifest.get("yaml_file") != "map.yaml" or manifest.get("image_file") != "map.pgm":
        raise ValueError("Map manifest file names are invalid")

    try:
        metadata = yaml.safe_load(yaml_bytes.decode("utf-8")) or {}
    except Exception as exc:
        raise ValueError(f"Invalid map.yaml: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError("map.yaml must contain a mapping")
    if metadata.get("image") != "map.pgm":
        raise ValueError("map.yaml image must reference map.pgm")
    try:
        resolution = float(metadata["resolution"])
        origin = [float(value) for value in metadata["origin"]]
    except Exception as exc:
        raise ValueError(f"map.yaml requires numeric resolution and origin: {exc}") from exc
    if resolution <= 0 or len(origin) < 3:
        raise ValueError("map.yaml resolution must be positive and origin must have three values")
    width, height, _ = read_pgm(image_bytes)

    logical_name = safe_map_name(str(logical_name_override or manifest.get("logical_name") or "map"))
    yaml_hash = hashlib.sha256(yaml_bytes).hexdigest()
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    bundle_hash = hashlib.sha256(f"{yaml_hash}:{image_hash}".encode("ascii")).hexdigest()
    expected_hash = manifest.get("bundle_sha256")
    if manifest.get("yaml_sha256") not in (None, yaml_hash):
        raise ValueError("map.yaml hash does not match manifest")
    if manifest.get("image_sha256") not in (None, image_hash):
        raise ValueError("map.pgm hash does not match manifest")
    if expected_hash and expected_hash != bundle_hash:
        raise ValueError("Map bundle hash does not match manifest")
    return MapBundle(
        logical_name=logical_name,
        yaml_bytes=yaml_bytes,
        image_bytes=image_bytes,
        yaml_sha256=yaml_hash,
        image_sha256=image_hash,
        bundle_sha256=bundle_hash,
        resolution=resolution,
        origin=origin[:3],
        width=width,
        height=height,
        manifest=manifest,
    )


def build_bundle(
    logical_name: str,
    yaml_bytes: bytes,
    image_bytes: bytes,
    version: Optional[int] = None,
    map_id: Optional[str] = None,
) -> bytes:
    yaml_hash = hashlib.sha256(yaml_bytes).hexdigest()
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    bundle_hash = hashlib.sha256(f"{yaml_hash}:{image_hash}".encode("ascii")).hexdigest()
    manifest = {
        "schema_version": 1,
        "logical_name": safe_map_name(logical_name),
        "yaml_file": "map.yaml",
        "image_file": "map.pgm",
        "yaml_sha256": yaml_hash,
        "image_sha256": image_hash,
        "bundle_sha256": bundle_hash,
    }
    if version is not None:
        manifest["version"] = int(version)
    if map_id is not None:
        manifest["map_id"] = str(map_id)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True).encode("utf-8"))
        archive.writestr("map.yaml", yaml_bytes)
        archive.writestr("map.pgm", image_bytes)
    return output.getvalue()


def normalize_yaml(yaml_bytes: bytes) -> bytes:
    metadata = yaml.safe_load(yaml_bytes.decode("utf-8")) or {}
    metadata["image"] = "map.pgm"
    return yaml.safe_dump(metadata, sort_keys=False).encode("utf-8")


def read_pgm(data: bytes) -> tuple[int, int, bytes]:
    if not data.startswith((b"P5", b"P2")):
        raise ValueError("Unsupported PGM format")
    offset = 0
    tokens: list[bytes] = []
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
        raise ValueError("Could not parse PGM header")
    magic, width_raw, height_raw, max_raw = tokens
    width, height, max_value = int(width_raw), int(height_raw), int(max_raw)
    if width <= 0 or height <= 0 or max_value <= 0 or max_value > 65535:
        raise ValueError("Invalid PGM dimensions or max value")
    if magic == b"P5":
        if max_value > 255:
            raise ValueError("16-bit PGM maps are not supported")
        if offset >= len(data) or data[offset] not in b" \t\r\n":
            raise ValueError("PGM header has no payload separator")
        offset += 2 if data[offset:offset + 2] == b"\r\n" else 1
        pixels = data[offset:offset + width * height]
    else:
        clean = re.sub(rb"#.*", b"", data[offset:])
        values = [int(value) for value in clean.split()]
        pixels = bytes(int(value * 255 / max_value) for value in values[:width * height])
    if len(pixels) != width * height:
        raise ValueError("PGM payload size mismatch")
    if max_value != 255 and magic == b"P5":
        pixels = bytes(int(value * 255 / max_value) for value in pixels)
    return width, height, pixels


def pgm_to_png(data: bytes) -> bytes:
    width, height, pixels = read_pgm(data)
    raw = b"".join(b"\x00" + pixels[row * width:(row + 1) * width] for row in range(height))

    def chunk(kind: bytes, value: bytes) -> bytes:
        return struct.pack("!I", len(value)) + kind + value + struct.pack("!I", binascii.crc32(kind + value) & 0xFFFFFFFF)

    return b"".join([
        b"\x89PNG\r\n\x1a\n",
        chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 0, 0, 0, 0)),
        chunk(b"IDAT", zlib.compress(raw, level=6)),
        chunk(b"IEND", b""),
    ])
