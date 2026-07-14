"""Allow-listed, validated robot tools exposed to the language model."""
from __future__ import annotations

import asyncio
import base64
import math
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent_client import FleetAgentClient
from ..errors import ApiError
from ..models import Robot
from ..repositories import RobotRepository


class EmptyArgs(BaseModel):
    pass


class LightsArgs(BaseModel):
    left: bool
    right: bool


class BeepArgs(BaseModel):
    duration_ms: int = Field(ge=50, le=1000)


class DriveArgs(BaseModel):
    direction: Literal["forward", "backward", "left", "right"]
    duration_ms: int = Field(ge=100, le=1000)


class TurnAngleArgs(BaseModel):
    direction: Literal["left", "right"]
    angle_degrees: int = Field(ge=15, le=180)


class ObserveArgs(BaseModel):
    question: str = Field(default="你看到了什么？", min_length=1, max_length=200)


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {"type": "function", "name": "get_robot_status", "description": "读取小车当前模式、运动和传感器状态。", "parameters": EmptyArgs.model_json_schema()},
    {"type": "function", "name": "set_lights", "description": "设置小车左右前灯开关。", "parameters": LightsArgs.model_json_schema()},
    {"type": "function", "name": "beep", "description": "让小车蜂鸣一次，时长50到1000毫秒。", "parameters": BeepArgs.model_json_schema()},
    {"type": "function", "name": "drive_briefly", "description": "让小车短暂前进、后退或原地左右转，最长一秒。用户说大步、明显移动时使用800到1000毫秒。", "parameters": DriveArgs.model_json_schema()},
    {"type": "function", "name": "turn_by_angle", "description": "按用户明确指定的角度原地左转或右转，例如左转90度。仅在用户说出角度时使用。", "parameters": TurnAngleArgs.model_json_schema()},
    {"type": "function", "name": "stop", "description": "立即停止小车运动。", "parameters": EmptyArgs.model_json_schema()},
    {"type": "function", "name": "observe", "description": "拍摄当前相机画面并结合视觉检测回答关于周围环境的问题。", "parameters": ObserveArgs.model_json_schema()},
]


class RobotToolExecutor:
    MODELS = {
        "get_robot_status": EmptyArgs,
        "set_lights": LightsArgs,
        "beep": BeepArgs,
        "drive_briefly": DriveArgs,
        "turn_by_angle": TurnAngleArgs,
        "stop": EmptyArgs,
        "observe": ObserveArgs,
    }

    def __init__(self, agent: FleetAgentClient, max_image_bytes: int = 4 * 1024 * 1024):
        self.agent = agent
        self.max_image_bytes = max_image_bytes

    async def execute(self, session: AsyncSession, robot_id: str, name: str, arguments: str | dict) -> dict[str, Any]:
        model = self.MODELS.get(name)
        if model is None:
            raise ApiError(400, "unknown_voice_tool", f"Tool '{name}' is not allowed")
        parsed = model.model_validate_json(arguments) if isinstance(arguments, str) else model.model_validate(arguments)
        robot = await RobotRepository.get(session, robot_id)
        if robot is None or not robot.enabled:
            raise ApiError(404, "robot_not_available", f"Robot '{robot_id}' is unavailable")
        if name == "get_robot_status":
            return await self.agent.status(robot)
        if name == "set_lights":
            return await self.agent.request(robot, "POST", "/api/control/lights", parsed.model_dump())
        if name == "beep":
            return await self.agent.request(robot, "POST", "/api/control/buzzer", {"enabled": True, **parsed.model_dump()})
        if name == "stop":
            return await self.agent.request(robot, "POST", "/api/control/stop")
        if name == "drive_briefly":
            return await self._drive(robot, parsed)
        if name == "turn_by_angle":
            return await self._turn_by_angle(robot, parsed)
        if name == "observe":
            return await self._observe(robot, parsed)
        raise AssertionError(name)

    async def _drive(self, robot: Robot, args: DriveArgs) -> dict[str, Any]:
        vectors = {
            "forward": (0.22, 0.0),
            "backward": (-0.22, 0.0),
            "left": (0.0, 0.9),
            "right": (0.0, -0.9),
        }
        linear_x, angular_z = vectors[args.direction]
        try:
            result = await self.agent.request(robot, "POST", "/api/control/manual_cmd", {
                "linear_x": linear_x,
                "linear_y": 0.0,
                "angular_z": angular_z,
                "ttl_ms": args.duration_ms,
                "source": "doubao-voice",
            })
            await asyncio.sleep(args.duration_ms / 1000)
            return {"ok": True, "command": args.model_dump(), "agent": result}
        finally:
            try:
                await self.agent.request(robot, "POST", "/api/control/stop")
            except Exception:
                pass

    async def _turn_by_angle(self, robot: Robot, args: TurnAngleArgs) -> dict[str, Any]:
        # Open-loop timing is intentionally conservative: the 15% compensation
        # offsets drivetrain stiction while the vehicle TTL remains the hard stop.
        angular_speed = 0.9
        duration_ms = round(math.radians(args.angle_degrees) / angular_speed * 1000 * 1.15)
        angular_z = angular_speed if args.direction == "left" else -angular_speed
        try:
            result = await self.agent.request(robot, "POST", "/api/control/manual_cmd", {
                "linear_x": 0.0,
                "linear_y": 0.0,
                "angular_z": angular_z,
                "ttl_ms": duration_ms,
                "source": "doubao-voice-angle",
            })
            await asyncio.sleep(duration_ms / 1000)
            return {
                "ok": True,
                "command": args.model_dump(),
                "duration_ms": duration_ms,
                "agent": result,
            }
        finally:
            try:
                await self.agent.request(robot, "POST", "/api/control/stop")
            except Exception:
                pass

    async def _observe(self, robot: Robot, args: ObserveArgs) -> dict[str, Any]:
        image, vision = await asyncio.gather(
            self.agent.video_sample(robot),
            self.agent.vision_capture(robot),
        )
        if len(image) > self.max_image_bytes:
            raise ApiError(413, "voice_image_too_large", "Camera image exceeds voice image limit")
        return {
            "ok": True,
            "question": args.question,
            "vision": vision,
            "_image_data_url": "data:image/jpeg;base64," + base64.b64encode(image).decode(),
        }
