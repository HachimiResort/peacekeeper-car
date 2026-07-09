"""HTTP request and response models."""
from typing import Optional

from pydantic import BaseModel, Field


class CmdVelRequest(BaseModel):
    linear_x: float = 0.0
    linear_y: float = 0.0
    angular_z: float = 0.0
    ttl_ms: int = Field(default=500, ge=100, le=5000)
    source: str = "web"


class ProcessRequest(BaseModel):
    process: str


class SaveMapRequest(BaseModel):
    name: str = Field(default="map", min_length=1, max_length=64)


class NavigationStartRequest(BaseModel):
    map_name: str = Field(default="map", min_length=1, max_length=64)


class NavigationPoseRequest(BaseModel):
    map_name: Optional[str] = Field(default=None, max_length=64)
    x: float
    y: float
    yaw: float = 0.0


class SnapshotResponse(BaseModel):
    ok: bool
    path: Optional[str] = None
    message: Optional[str] = None
