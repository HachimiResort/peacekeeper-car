import asyncio
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .map_bundle import MapBundle, build_bundle, parse_bundle, pgm_to_png
from .models import StoredMap
from .repositories import MapRepository


class MapService:
    def __init__(self, storage_dir: Path, max_bytes: int):
        self.storage_dir = storage_dir
        self.max_bytes = max_bytes
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        (self.storage_dir / ".staging").mkdir(parents=True, exist_ok=True)

    def ready(self) -> bool:
        return self.storage_dir.is_dir() and os.access(self.storage_dir, os.R_OK | os.W_OK | os.X_OK)

    async def ingest(
        self,
        session: AsyncSession,
        payload: bytes,
        source_robot_id: Optional[str],
        logical_name_override: Optional[str] = None,
    ) -> tuple[StoredMap, bool]:
        bundle = await asyncio.to_thread(parse_bundle, payload, self.max_bytes, logical_name_override)
        existing = await MapRepository.by_hash(session, bundle.logical_name, bundle.bundle_sha256)
        if existing is not None:
            return existing, False
        for _ in range(3):
            version = await MapRepository.next_version(session, bundle.logical_name)
            map_id = uuid.uuid4()
            storage_key = str(map_id)
            try:
                await asyncio.to_thread(self._write_storage, storage_key, bundle, version, map_id)
                stored = StoredMap(
                    id=map_id,
                    logical_name=bundle.logical_name,
                    version=version,
                    yaml_sha256=bundle.yaml_sha256,
                    image_sha256=bundle.image_sha256,
                    bundle_sha256=bundle.bundle_sha256,
                    resolution=bundle.resolution,
                    origin=bundle.origin,
                    width=bundle.width,
                    height=bundle.height,
                    storage_key=storage_key,
                    source_robot_id=source_robot_id,
                )
                session.add(stored)
                await session.commit()
                await session.refresh(stored)
                return stored, True
            except IntegrityError:
                await session.rollback()
                await asyncio.to_thread(self._remove_storage, storage_key)
                existing = await MapRepository.by_hash(session, bundle.logical_name, bundle.bundle_sha256)
                if existing is not None:
                    return existing, False
            except Exception:
                await session.rollback()
                await asyncio.to_thread(self._remove_storage, storage_key)
                raise
        raise RuntimeError("Could not allocate a map version after concurrent imports")

    async def bundle_for(self, stored: StoredMap) -> bytes:
        yaml_bytes, image_bytes = await asyncio.to_thread(self._read_files, stored.storage_key)
        return await asyncio.to_thread(
            build_bundle,
            stored.logical_name,
            yaml_bytes,
            image_bytes,
            stored.version,
            str(stored.id),
        )

    async def preview(self, stored: StoredMap) -> bytes:
        _, image_bytes = await asyncio.to_thread(self._read_files, stored.storage_key)
        return await asyncio.to_thread(pgm_to_png, image_bytes)

    def _write_storage(self, storage_key: str, bundle: MapBundle, version: int, map_id: uuid.UUID) -> None:
        staging_root = self.storage_dir / ".staging"
        temp_dir = Path(tempfile.mkdtemp(prefix=f"{storage_key}-", dir=staging_root))
        target = self.storage_dir / storage_key
        try:
            (temp_dir / "map.yaml").write_bytes(bundle.yaml_bytes)
            (temp_dir / "map.pgm").write_bytes(bundle.image_bytes)
            manifest = {
                "schema_version": 1,
                "map_id": str(map_id),
                "logical_name": bundle.logical_name,
                "version": version,
                "yaml_file": "map.yaml",
                "image_file": "map.pgm",
                "yaml_sha256": bundle.yaml_sha256,
                "image_sha256": bundle.image_sha256,
                "bundle_sha256": bundle.bundle_sha256,
                "resolution": bundle.resolution,
                "origin": bundle.origin,
                "width": bundle.width,
                "height": bundle.height,
            }
            (temp_dir / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            temp_dir.replace(target)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def _read_files(self, storage_key: str) -> tuple[bytes, bytes]:
        directory = self.storage_dir / storage_key
        return (directory / "map.yaml").read_bytes(), (directory / "map.pgm").read_bytes()

    def _remove_storage(self, storage_key: str) -> None:
        shutil.rmtree(self.storage_dir / storage_key, ignore_errors=True)
