"""Map save helpers."""
from __future__ import annotations

import re
from pathlib import Path

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
        result = self.process_manager.run_ros_command(command, timeout_s=30.0)
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
        }

    @staticmethod
    def _safe_name(name: str) -> str:
        value = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
        value = value.strip("._-")
        return value or "map"
