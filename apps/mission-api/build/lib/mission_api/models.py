import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from .db import Base

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Robot(Base):
    __tablename__ = "robots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(512), unique=True)
    role: Mapped[str] = mapped_column(String(64), default="robot")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_type: Mapped[str] = mapped_column(String(64), index=True)
    robot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("robots.id"), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    result_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    robot_id: Mapped[str] = mapped_column(ForeignKey("robots.id"), index=True)
    mission_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("missions.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(32), default="info")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), unique=True)
    state: Mapped[str] = mapped_column(String(32), default="pending")
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class StoredMap(Base):
    __tablename__ = "maps"
    __table_args__ = (
        UniqueConstraint("logical_name", "version", name="uq_map_name_version"),
        UniqueConstraint("logical_name", "bundle_sha256", name="uq_map_name_bundle"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    logical_name: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer)
    yaml_sha256: Mapped[str] = mapped_column(String(64))
    image_sha256: Mapped[str] = mapped_column(String(64))
    bundle_sha256: Mapped[str] = mapped_column(String(64), index=True)
    resolution: Mapped[float] = mapped_column(Float)
    origin: Mapped[list[float]] = mapped_column(JSON_TYPE)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(256), unique=True)
    source_robot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("robots.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MapDeployment(Base):
    __tablename__ = "map_deployments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    map_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("maps.id"), index=True)
    robot_id: Mapped[str] = mapped_column(ForeignKey("robots.id"), index=True)
    state: Mapped[str] = mapped_column(String(32), default="pending")
    installed_name: Mapped[Optional[str]] = mapped_column(String(192), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
