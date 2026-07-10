import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class RobotCreate(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=128)
    base_url: HttpUrl
    role: str = Field(default="robot", min_length=1, max_length=64)
    enabled: bool = True
    capabilities: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: HttpUrl) -> HttpUrl:
        return HttpUrl(str(value).rstrip("/"))


class RobotUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    base_url: Optional[HttpUrl] = None
    role: Optional[str] = Field(default=None, min_length=1, max_length=64)
    enabled: Optional[bool] = None
    capabilities: Optional[dict[str, Any]] = None


class RobotView(BaseModel):
    id: str
    name: str
    base_url: str
    role: str
    enabled: bool
    capabilities: dict[str, Any]
    last_seen: Optional[datetime]
    online: bool = False
    runtime_status: Optional[dict[str, Any]] = None


class ManualCommand(BaseModel):
    linear_x: float = 0.0
    linear_y: float = 0.0
    angular_z: float = 0.0
    ttl_ms: int = Field(default=500, ge=100, le=5000)
    source: str = "mission-api"


class MapNameRequest(BaseModel):
    map_name: str = Field(min_length=1, max_length=128)


class MapSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class PoseRequest(BaseModel):
    map_name: Optional[str] = Field(default=None, max_length=128)
    x: float
    y: float
    yaw: float = 0.0


class PatrolPoint(BaseModel):
    name: Optional[str] = Field(default=None, max_length=64)
    x: float
    y: float
    yaw: float = 0.0
    dwell_s: float = Field(default=0.0, ge=0.0, le=3600.0)


class PatrolRequest(BaseModel):
    route_name: Optional[str] = Field(default=None, max_length=64)
    map_name: Optional[str] = Field(default=None, max_length=128)
    loop: Optional[bool] = None
    points: list[PatrolPoint] = Field(default_factory=list)


class RobotEventRequest(BaseModel):
    robot_id: str
    mission_id: Optional[uuid.UUID] = None
    event_type: str = Field(min_length=1, max_length=64)
    severity: str = Field(default="info", max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime
    create_alert: bool = False


class AlertConfirmRequest(BaseModel):
    confirmed_by: str = Field(min_length=1, max_length=128)
    resolution: Optional[str] = None


class MapImportRequest(BaseModel):
    robot_id: str
    map_name: str = Field(min_length=1, max_length=128)
    logical_name: Optional[str] = Field(default=None, max_length=128)


class MapDispatchRequest(BaseModel):
    robot_ids: list[str] = Field(min_length=1)


class RobotResponse(BaseModel):
    id: str
    name: str
    base_url: str
    role: str
    enabled: bool
    capabilities: dict[str, Any]
    last_seen: Optional[str] = None
    online: bool = False
    runtime_status: Optional[dict[str, Any]] = None
    runtime_error: Optional[str] = None


class RobotListResponse(BaseModel):
    robots: list[RobotResponse]


class MissionResponse(BaseModel):
    id: str
    mission_type: str
    robot_id: Optional[str] = None
    state: str
    request: dict[str, Any]
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class MissionListResponse(BaseModel):
    missions: list[MissionResponse]
    total: int
    limit: int
    offset: int


class EventResponse(BaseModel):
    id: str
    robot_id: str
    mission_id: Optional[str] = None
    event_type: str
    severity: str
    payload: dict[str, Any]
    occurred_at: Optional[str] = None
    received_at: Optional[str] = None


class EventListResponse(BaseModel):
    events: list[EventResponse]
    total: int
    limit: int
    offset: int


class AlertResponse(BaseModel):
    id: str
    event_id: str
    state: str
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    resolution: Optional[str] = None
    event: EventResponse


class AlertListResponse(BaseModel):
    alerts: list[AlertResponse]
    total: int
    limit: int
    offset: int


class MapResponse(BaseModel):
    id: str
    logical_name: str
    version: int
    yaml_sha256: str
    image_sha256: str
    bundle_sha256: str
    resolution: float
    origin: list[float]
    width: int
    height: int
    source_robot_id: Optional[str] = None
    created_at: Optional[str] = None


class MapListResponse(BaseModel):
    maps: list[MapResponse]


class DeploymentResponse(BaseModel):
    id: str
    map_id: str
    robot_id: str
    state: str
    installed_name: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    map: dict[str, Any]
    robot: dict[str, Any]


class DeploymentListResponse(BaseModel):
    deployments: list[DeploymentResponse]
    total: int
    limit: int
    offset: int


class OverviewResponse(BaseModel):
    robots: dict[str, int]
    missions: dict[str, int]
    alerts: dict[str, int]
    maps: dict[str, Any]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
