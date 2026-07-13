"""HTTP request and response models."""
from typing import List, Optional

from pydantic import BaseModel, Field


class CmdVelRequest(BaseModel):
    linear_x: float = 0.0
    linear_y: float = 0.0
    angular_z: float = 0.0
    ttl_ms: int = Field(default=500, ge=100, le=5000)
    source: str = "web"


class LightControlRequest(BaseModel):
    """Set the iCAR headlight state through the shared Rosmaster serial link."""

    left: bool = True
    right: bool = True
    # The firmware treats zero as a persistent state; 1..255 auto-turns on
    # lights off after the requested number of milliseconds.
    duration_ms: int = Field(default=0, ge=0, le=255)


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


class PatrolPointRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=64)
    x: float
    y: float
    yaw: float = 0.0
    dwell_s: float = Field(default=0.0, ge=0.0, le=3600.0)


class PatrolStartRequest(BaseModel):
    route_name: Optional[str] = Field(default=None, max_length=64)
    map_name: Optional[str] = Field(default=None, max_length=64)
    loop: Optional[bool] = None
    points: List[PatrolPointRequest] = Field(default_factory=list)


class HazardMonitorRequest(BaseModel):
    enabled: bool


class HazardHoldRequest(BaseModel):
    hold: bool = True


class SnapshotResponse(BaseModel):
    ok: bool
    path: Optional[str] = None
    message: Optional[str] = None
