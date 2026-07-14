"""Safe, single-process local audio playback for fleet-agent."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from .config import AudioConfig


class AudioService:
    """Play pre-provisioned audio assets without exposing a shell endpoint."""

    _EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a"}

    def __init__(self, config: AudioConfig, data_dir: Path):
        self.config = config
        self.asset_dir = Path(config.asset_dir).expanduser() if config.asset_dir else data_dir / "audio"
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._asset: Optional[str] = None
        self._loop = False
        self._volume = 100
        self._started_at: Optional[float] = None
        self._last_error: Optional[str] = None

    def assets(self) -> dict:
        entries = []
        for path in sorted(self.asset_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in self._EXTENSIONS:
                continue
            if path.stat().st_size > self.config.max_asset_bytes:
                continue
            entries.append({"name": path.name, "bytes": path.stat().st_size})
        return {"ok": True, "asset_dir": str(self.asset_dir), "assets": entries}

    def install(self, filename: str, payload: bytes) -> dict:
        """Atomically install one validated asset into the playback directory."""
        with self._lock:
            candidate = Path(filename)
            if candidate.name != filename or candidate.suffix.lower() not in self._EXTENSIONS:
                raise ValueError("filename must be a supported audio filename")
            if not payload:
                raise ValueError("audio asset is empty")
            if len(payload) > self.config.max_asset_bytes:
                raise ValueError("audio asset exceeds the configured size limit")
            if self._asset == candidate.name:
                self._stop_unlocked()
            target = self.asset_dir / candidate.name
            fd, staging_name = tempfile.mkstemp(prefix=".audio-", suffix=".staging", dir=str(self.asset_dir))
            try:
                with os.fdopen(fd, "wb") as staging:
                    staging.write(payload)
                    staging.flush()
                    os.fsync(staging.fileno())
                os.replace(staging_name, target)
            except OSError:
                try:
                    os.unlink(staging_name)
                except OSError:
                    pass
                raise
            return {"ok": True, "asset": {"name": target.name, "bytes": target.stat().st_size}}

    def play(self, asset: str, loop: bool = False, volume: int = 100) -> dict:
        with self._lock:
            try:
                normalized_volume = int(volume)
            except (TypeError, ValueError) as exc:
                raise ValueError("volume must be an integer from 0 to 100") from exc
            if not 0 <= normalized_volume <= 100:
                raise ValueError("volume must be an integer from 0 to 100")
            player = self._player_path()
            if player is None:
                raise RuntimeError("Audio playback is disabled or ffplay is not available")
            path = self._asset_path(asset)
            self._stop_unlocked()
            command = [player, "-nodisp", "-autoexit", "-loglevel", "error", "-volume", str(normalized_volume)]
            if loop:
                command.extend(["-loop", "0"])
            command.append(str(path))
            try:
                self._process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
            except OSError as exc:
                self._last_error = str(exc)
                raise RuntimeError(f"Unable to start audio player: {exc}") from exc
            self._asset = path.name
            self._loop = bool(loop)
            self._volume = normalized_volume
            self._started_at = time.time()
            self._last_error = None
            return {"ok": True, **self._status_unlocked()}

    def stop(self) -> dict:
        with self._lock:
            self._stop_unlocked()
            return {"ok": True, **self._status_unlocked()}

    def status(self) -> dict:
        with self._lock:
            return self._status_unlocked()

    def shutdown(self) -> None:
        with self._lock:
            self._stop_unlocked()

    def _asset_path(self, asset: str) -> Path:
        candidate = Path(asset)
        if candidate.name != asset or candidate.suffix.lower() not in self._EXTENSIONS:
            raise ValueError("asset must be a supported filename in the audio directory")
        path = self.asset_dir / candidate.name
        if not path.is_file():
            raise FileNotFoundError(f"Audio asset '{candidate.name}' was not found")
        if path.stat().st_size > self.config.max_asset_bytes:
            raise ValueError(f"Audio asset '{candidate.name}' exceeds the configured size limit")
        return path

    def _player_path(self) -> Optional[str]:
        if not self.config.enabled:
            return None
        player = self.config.player_command.strip()
        if not player:
            return None
        if os.path.isabs(player):
            return player if os.path.isfile(player) and os.access(player, os.X_OK) else None
        return shutil.which(player)

    def _stop_unlocked(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
        if process is not None and process.stderr is not None:
            process.stderr.close()
        self._process = None
        self._asset = None
        self._loop = False
        self._started_at = None

    def _status_unlocked(self) -> dict:
        process = self._process
        if process is not None and process.poll() is not None:
            if process.returncode not in (0, None):
                self._last_error = f"ffplay exited with code {process.returncode}"
            if process.stderr is not None:
                process.stderr.close()
            self._process = None
            self._asset = None
            self._loop = False
            self._started_at = None
            process = None
        return {
            "enabled": self.config.enabled,
            "available": self._player_path() is not None,
            "playing": process is not None,
            "asset": self._asset,
            "pid": process.pid if process is not None else None,
            "loop": self._loop,
            "volume": self._volume,
            "started_at": self._started_at,
            "last_error": self._last_error,
        }
