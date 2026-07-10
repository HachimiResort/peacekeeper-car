"""Configuration loading for the fleet-agent."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

try:
    import yaml
except Exception:  # pragma: no cover - dependency is installed on the car via requirements.txt.
    yaml = None


@dataclass
class RosConfig:
    distro_setup: str = "/opt/ros/foxy/setup.bash"
    workspace_setup: str = "/root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash"
    cmd_vel_topic: str = "/cmd_vel"
    scan_topic: str = "/scan"
    map_topic: str = "/map"
    odom_topic: str = "/odom"
    odom_frame: str = "odom"
    base_frame: str = "base_footprint"
    base_link_frame: str = "base_link"
    publish_odom: bool = True
    publish_tf: bool = True
    odom_period_s: float = 0.05
    odom_linear_x_scale: float = 1.0
    odom_linear_y_scale: float = 1.0
    odom_angular_z_scale: float = 1.0
    mock_cmd_vel: bool = False


@dataclass
class SafetyConfig:
    default_ttl_ms: int = 500
    watchdog_period_s: float = 0.1
    max_linear_x: float = 0.45
    max_linear_y: float = 0.45
    max_angular_z: float = 1.4


@dataclass
class VideoConfig:
    device: int = 0
    width: int = 640
    height: int = 480
    fps: int = 20


@dataclass
class ControlConfig:
    backend: str = "rosmaster"
    rosmaster_port: str = "/dev/myserial"
    rosmaster_speed: int = 25
    direct_deadband: float = 0.01
    direct_motion_mode: str = "motion"
    manual_override_s: float = 0.8


@dataclass
class ProcessConfig:
    name: str
    command: str
    auto_start: bool = False


@dataclass
class MappingConfig:
    saver_command: str = (
        "ros2 run nav2_map_server map_saver_cli "
        "-f {map_path} --ros-args -p save_map_timeout:=120000"
    )


@dataclass
class PatrolConfig:
    routes_file: str = "/root/peacekeeper-car/configs/patrol_routes.yaml"
    goal_timeout_s: float = 180.0
    cancel_timeout_s: float = 5.0
    poll_period_s: float = 0.2


@dataclass
class SecurityConfig:
    require_token: bool = False
    shared_token: str = ""
    max_map_bytes: int = 64 * 1024 * 1024


@dataclass
class AgentConfig:
    host: str = "0.0.0.0"
    port: int = 8001
    workspace: str = "/root/yahboomcar_ros2_ws/yahboomcar_ws"
    data_dir: str = "/root/peacekeeper-car/data"
    ros: RosConfig = field(default_factory=RosConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    processes: Dict[str, ProcessConfig] = field(
        default_factory=lambda: {
            "lidar": ProcessConfig(
                name="lidar",
                command=(
                    "bash -lc 'ros2 launch sllidar_ros2 sllidar_launch.py & "
                    "ros2 run tf2_ros static_transform_publisher "
                    "-0.0455 5.258E-05 0.3059 3.14 0 0 base_link laser & wait'"
                ),
            ),
            "slam": ProcessConfig(
                name="slam",
                command="ros2 launch slam_gmapping slam_gmapping.launch.py",
            ),
        }
    )
    mapping: MappingConfig = field(default_factory=MappingConfig)
    patrol: PatrolConfig = field(default_factory=PatrolConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _to_dict(config: AgentConfig) -> dict:
    return {
        "host": config.host,
        "port": config.port,
        "workspace": config.workspace,
        "data_dir": config.data_dir,
        "ros": vars(config.ros),
        "safety": vars(config.safety),
        "video": vars(config.video),
        "control": vars(config.control),
        "processes": {key: vars(value) for key, value in config.processes.items()},
        "mapping": vars(config.mapping),
        "patrol": vars(config.patrol),
        "security": vars(config.security),
    }


def _from_dict(raw: dict) -> AgentConfig:
    processes = {
        key: ProcessConfig(
            name=value.get("name", key),
            command=value["command"],
            auto_start=bool(value.get("auto_start", False)),
        )
        for key, value in raw.get("processes", {}).items()
    }
    return AgentConfig(
        host=raw.get("host", "0.0.0.0"),
        port=int(raw.get("port", 8001)),
        workspace=raw.get("workspace", "/root/yahboomcar_ros2_ws/yahboomcar_ws"),
        data_dir=raw.get("data_dir", "/root/peacekeeper-car/data"),
        ros=RosConfig(**raw.get("ros", {})),
        safety=SafetyConfig(**raw.get("safety", {})),
        video=VideoConfig(**raw.get("video", {})),
        control=ControlConfig(**raw.get("control", {})),
        processes=processes or AgentConfig().processes,
        mapping=MappingConfig(**raw.get("mapping", {})),
        patrol=PatrolConfig(**raw.get("patrol", {})),
        security=SecurityConfig(**raw.get("security", {})),
    )


def load_config(path: Optional[str] = None) -> AgentConfig:
    default = AgentConfig()
    if not path:
        env_token = os.environ.get("PEACEKEEPER_SHARED_TOKEN")
        if env_token:
            default.security.shared_token = env_token
        return default

    if yaml is None:
        raise RuntimeError("PyYAML is required to load a config file. Install requirements.txt first.")

    config_path = Path(path).expanduser()
    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    merged = _deep_merge(_to_dict(default), loaded)
    config = _from_dict(merged)
    env_token = os.environ.get("PEACEKEEPER_SHARED_TOKEN")
    if env_token:
        config.security.shared_token = env_token
    return config
