"""Secure import/export of versioned map ZIP bundles."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

try:
    import yaml
except Exception:  # pragma: no cover - installed on the car through requirements.txt.
    yaml = None

from .mapping import MappingService

EXPECTED_FILES = {"manifest.json", "map.yaml", "map.pgm"}


def _require_yaml():
    if yaml is None:
        raise RuntimeError("PyYAML is required for map bundle import/export")
    return yaml


def build_map_bundle(yaml_path: Path, pgm_path: Path, logical_name: str) -> bytes:
    yaml_module = _require_yaml()
    metadata = yaml_module.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    if not isinstance(metadata, dict):
        raise ValueError("map.yaml must contain a mapping")
    metadata["image"] = "map.pgm"
    yaml_bytes = yaml_module.safe_dump(metadata, sort_keys=False).encode("utf-8")
    image_bytes = pgm_path.read_bytes()
    MappingService._read_pgm(pgm_path)
    yaml_hash = hashlib.sha256(yaml_bytes).hexdigest()
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    bundle_hash = _bundle_hash(yaml_hash, image_hash)
    manifest = {
        "schema_version": 1,
        "logical_name": MappingService._safe_name(logical_name),
        "yaml_file": "map.yaml",
        "image_file": "map.pgm",
        "yaml_sha256": yaml_hash,
        "image_sha256": image_hash,
        "bundle_sha256": bundle_hash,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True).encode("utf-8"))
        archive.writestr("map.yaml", yaml_bytes)
        archive.writestr("map.pgm", image_bytes)
    return output.getvalue()


def install_map_bundle(payload: bytes, maps_dir: Path, max_bytes: int) -> dict:
    yaml_module = _require_yaml()
    if len(payload) > max_bytes:
        raise ValueError(f"Compressed map bundle exceeds {max_bytes} bytes")
    manifest, yaml_bytes, image_bytes = _read_archive(payload, max_bytes)
    logical_name = MappingService._safe_name(str(manifest.get("logical_name") or "map"))
    try:
        version = int(manifest["version"])
    except Exception as exc:
        raise ValueError("Installed map bundle requires a positive integer version") from exc
    if version <= 0:
        raise ValueError("Installed map bundle requires a positive integer version")
    installed_name = f"{logical_name}__v{version}"
    yaml_path = maps_dir / f"{installed_name}.yaml"
    pgm_path = maps_dir / f"{installed_name}.pgm"
    manifest_path = maps_dir / f"{installed_name}.manifest.json"
    bundle_hash = str(manifest["bundle_sha256"])

    if yaml_path.exists() or pgm_path.exists() or manifest_path.exists():
        existing = _existing_hash(manifest_path)
        if existing == bundle_hash and yaml_path.exists() and pgm_path.exists():
            return {"ok": True, "name": installed_name, "idempotent": True, "bundle_sha256": bundle_hash}
        raise FileExistsError(f"Installed map name '{installed_name}' already exists with different content")

    staging_root = maps_dir / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="map-install-", dir=staging_root))
    try:
        temp_yaml = staging / "map.yaml"
        temp_pgm = staging / "map.pgm"
        metadata = yaml_module.safe_load(yaml_bytes.decode("utf-8")) or {}
        metadata["image"] = pgm_path.name
        temp_yaml.write_text(yaml_module.safe_dump(metadata, sort_keys=False), encoding="utf-8")
        temp_pgm.write_bytes(image_bytes)
        MappingService._read_pgm(temp_pgm)
        sidecar = dict(manifest)
        sidecar["installed_name"] = installed_name
        (staging / "manifest.json").write_text(json.dumps(sidecar, sort_keys=True), encoding="utf-8")

        os.replace(str(temp_pgm), str(pgm_path))
        os.replace(str(staging / "manifest.json"), str(manifest_path))
        os.replace(str(temp_yaml), str(yaml_path))
    except Exception:
        for path in (yaml_path, manifest_path, pgm_path):
            if not yaml_path.exists() and path.exists():
                path.unlink()
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"ok": True, "name": installed_name, "idempotent": False, "bundle_sha256": bundle_hash}


def _read_archive(payload: bytes, max_bytes: int):
    yaml_module = _require_yaml()
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload), "r")
    except Exception as exc:
        raise ValueError(f"Invalid map ZIP: {exc}") from exc
    with archive:
        infos = archive.infolist()
        if {item.filename for item in infos} != EXPECTED_FILES or len(infos) != len(EXPECTED_FILES):
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
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        yaml_bytes = archive.read("map.yaml")
        image_bytes = archive.read("map.pgm")
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must contain an object")
    if int(manifest.get("schema_version", 0)) != 1:
        raise ValueError("Unsupported map bundle schema_version")
    if manifest.get("yaml_file") != "map.yaml" or manifest.get("image_file") != "map.pgm":
        raise ValueError("Map manifest file names are invalid")
    try:
        metadata = yaml_module.safe_load(yaml_bytes.decode("utf-8")) or {}
    except Exception as exc:
        raise ValueError(f"Invalid map.yaml: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError("map.yaml must contain a mapping")
    if metadata.get("image") != "map.pgm":
        raise ValueError("map.yaml image must reference map.pgm")
    try:
        resolution = float(metadata.get("resolution", 0))
        origin = metadata.get("origin", [])
        valid_origin = isinstance(origin, (list, tuple)) and len(origin) >= 3
    except (TypeError, ValueError):
        resolution = 0
        valid_origin = False
    if resolution <= 0 or not valid_origin:
        raise ValueError("map.yaml resolution/origin are invalid")
    yaml_hash = hashlib.sha256(yaml_bytes).hexdigest()
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    bundle_hash = _bundle_hash(yaml_hash, image_hash)
    if manifest.get("yaml_sha256") != yaml_hash or manifest.get("image_sha256") != image_hash:
        raise ValueError("Map file hash does not match manifest")
    if manifest.get("bundle_sha256") != bundle_hash:
        raise ValueError("Map bundle hash does not match manifest")
    return manifest, yaml_bytes, image_bytes


def _bundle_hash(yaml_hash: str, image_hash: str) -> str:
    return hashlib.sha256(f"{yaml_hash}:{image_hash}".encode("ascii")).hexdigest()


def _existing_hash(path: Path) -> Optional[str]:
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("bundle_sha256"))
    except Exception:
        return None
