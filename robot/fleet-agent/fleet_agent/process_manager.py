"""Managed ROS2 process lifecycle."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

try:
    import psutil
except Exception:  # pragma: no cover - optional outside deployment.
    psutil = None

from .config import AgentConfig, ProcessConfig


class ProcessStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class ManagedProcess:
    key: str
    config: ProcessConfig
    status: ProcessStatus = ProcessStatus.STOPPED
    pid: Optional[int] = None
    started_at: Optional[float] = None
    error: Optional[str] = None
    handle: Optional[subprocess.Popen] = None

    def to_dict(self) -> dict:
        cpu = 0.0
        memory = 0.0
        if psutil is not None and self.pid and self.status == ProcessStatus.RUNNING:
            try:
                proc = psutil.Process(self.pid)
                if proc.is_running():
                    cpu = proc.cpu_percent(interval=None)
                    memory = proc.memory_info().rss / 1024 / 1024
            except Exception:
                pass
        return {
            "name": self.config.name,
            "pid": self.pid,
            "status": self.status.value,
            "started_at": self.started_at,
            "cpu": cpu,
            "memory_mb": memory,
            "error": self.error,
        }


class ProcessManager:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.processes: Dict[str, ManagedProcess] = {
            key: ManagedProcess(key=key, config=value)
            for key, value in config.processes.items()
        }
        self.lock = threading.RLock()

    def _shell_prefix(self) -> str:
        parts = []
        if self.config.ros.distro_setup:
            parts.append(f'if test -f "{self.config.ros.distro_setup}"; then source "{self.config.ros.distro_setup}"; fi')
        if self.config.ros.workspace_setup:
            parts.append(f'if test -f "{self.config.ros.workspace_setup}"; then source "{self.config.ros.workspace_setup}"; fi')
        parts.append(f'cd "{self.config.workspace}"')
        return " && ".join(parts)

    def _bash_command(self, command: str) -> str:
        return f"{self._shell_prefix()} && exec {command}"

    def start(self, key: str) -> dict:
        with self.lock:
            if key not in self.processes:
                raise KeyError(f"Unknown process: {key}")
            managed = self.processes[key]
            self._refresh_one(managed)
            if managed.status == ProcessStatus.RUNNING:
                return managed.to_dict()

            managed.status = ProcessStatus.STARTING
            managed.error = None
            try:
                handle = subprocess.Popen(
                    ["bash", "-lc", self._bash_command(managed.config.command)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid,
                    cwd=self.config.workspace if os.path.isdir(self.config.workspace) else None,
                    text=True,
                )
                managed.handle = handle
                managed.pid = handle.pid
                managed.started_at = time.time()
                time.sleep(0.5)
                self._refresh_one(managed)
                return managed.to_dict()
            except Exception as exc:
                managed.status = ProcessStatus.ERROR
                managed.error = str(exc)
                raise

    def stop(self, key: str, timeout_s: float = 4.0) -> dict:
        with self.lock:
            if key not in self.processes:
                raise KeyError(f"Unknown process: {key}")
            managed = self.processes[key]
            self._stop_one(managed, timeout_s=timeout_s)
            return managed.to_dict()

    def stop_all(self) -> Dict[str, dict]:
        with self.lock:
            # Stop higher-level features before base drivers.
            ordered = list(self.processes.keys())
            for key in reversed(ordered):
                self._stop_one(self.processes[key])
            return self.status()

    def status(self) -> Dict[str, dict]:
        with self.lock:
            for managed in self.processes.values():
                self._refresh_one(managed)
            return {key: value.to_dict() for key, value in self.processes.items()}

    def start_auto_processes(self) -> None:
        for key, managed in self.processes.items():
            if managed.config.auto_start:
                self.start(key)

    def run_ros_command(self, command: str, timeout_s: float = 10.0) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-lc", f"{self._shell_prefix()} && {command}"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )

    def topic_active(self, topic: str) -> bool:
        try:
            result = self.run_ros_command(f"ros2 topic info {topic}", timeout_s=1.5)
        except Exception:
            return False
        if result.returncode != 0:
            return False
        output = result.stdout.lower()
        return "publisher count: 0" not in output and ("publisher count:" in output or "type:" in output)

    def _refresh_one(self, managed: ManagedProcess) -> None:
        handle = managed.handle
        if not handle:
            if managed.status not in (ProcessStatus.ERROR, ProcessStatus.STOPPED):
                managed.status = ProcessStatus.STOPPED
            return
        if handle.poll() is None:
            managed.status = ProcessStatus.RUNNING
            return
        if managed.status not in (ProcessStatus.STOPPING, ProcessStatus.STOPPED):
            managed.status = ProcessStatus.ERROR
            managed.error = f"Exited with code {handle.returncode}"
        managed.pid = None
        managed.handle = None

    def _stop_one(self, managed: ManagedProcess, timeout_s: float = 4.0) -> None:
        handle = managed.handle
        if not handle or handle.poll() is not None:
            managed.status = ProcessStatus.STOPPED
            managed.pid = None
            managed.handle = None
            return

        managed.status = ProcessStatus.STOPPING
        try:
            os.killpg(os.getpgid(handle.pid), signal.SIGTERM)
            handle.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(handle.pid), signal.SIGKILL)
            handle.wait(timeout=2)
        except Exception as exc:
            managed.status = ProcessStatus.ERROR
            managed.error = str(exc)
            return
        finally:
            managed.pid = None
            managed.handle = None
        managed.status = ProcessStatus.STOPPED
