import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Alert, Event, MapDeployment, Mission, Robot, StoredMap


class RobotRepository:
    @staticmethod
    async def list(session: AsyncSession, enabled_only: bool = False) -> list[Robot]:
        statement = select(Robot).order_by(Robot.id)
        if enabled_only:
            statement = statement.where(Robot.enabled.is_(True))
        return list((await session.scalars(statement)).all())

    @staticmethod
    async def get(session: AsyncSession, robot_id: str) -> Optional[Robot]:
        return await session.get(Robot, robot_id)

    @staticmethod
    async def create(session: AsyncSession, **values) -> Robot:
        robot = Robot(**values)
        session.add(robot)
        await session.commit()
        await session.refresh(robot)
        return robot

    @staticmethod
    async def update(session: AsyncSession, robot: Robot, values: dict) -> Robot:
        for key, value in values.items():
            setattr(robot, key, value)
        await session.commit()
        await session.refresh(robot)
        return robot


class MissionRepository:
    @staticmethod
    async def create(session: AsyncSession, mission_type: str, robot_id: Optional[str], payload: dict) -> Mission:
        now = datetime.now(timezone.utc)
        mission = Mission(
            mission_type=mission_type,
            robot_id=robot_id,
            request_payload=payload,
            state="running",
            started_at=now,
        )
        session.add(mission)
        await session.commit()
        await session.refresh(mission)
        return mission

    @staticmethod
    async def finish(session: AsyncSession, mission: Mission, result: dict) -> Mission:
        mission.state = "completed"
        mission.result_payload = result
        mission.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return mission

    @staticmethod
    async def fail(session: AsyncSession, mission: Mission, error: str) -> Mission:
        mission.state = "failed"
        mission.error = error
        mission.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return mission

    @staticmethod
    async def reconcile_interrupted(session: AsyncSession) -> int:
        statement = (
            update(Mission)
            .where(Mission.state.in_(["pending", "running"]))
            .values(state="failed", error="service_restart", finished_at=datetime.now(timezone.utc))
        )
        result = await session.execute(statement)
        await session.commit()
        return int(result.rowcount or 0)


class MapRepository:
    @staticmethod
    async def list(session: AsyncSession) -> list[StoredMap]:
        statement = select(StoredMap).order_by(StoredMap.logical_name, StoredMap.version.desc())
        return list((await session.scalars(statement)).all())

    @staticmethod
    async def get(session: AsyncSession, map_id: uuid.UUID) -> Optional[StoredMap]:
        return await session.get(StoredMap, map_id)

    @staticmethod
    async def by_hash(session: AsyncSession, logical_name: str, bundle_hash: str) -> Optional[StoredMap]:
        statement = select(StoredMap).where(
            StoredMap.logical_name == logical_name,
            StoredMap.bundle_sha256 == bundle_hash,
        )
        return await session.scalar(statement)

    @staticmethod
    async def next_version(session: AsyncSession, logical_name: str) -> int:
        value = await session.scalar(select(func.max(StoredMap.version)).where(StoredMap.logical_name == logical_name))
        return int(value or 0) + 1


class EventRepository:
    @staticmethod
    async def list(session: AsyncSession, limit: int = 200) -> list[Event]:
        statement = select(Event).order_by(Event.received_at.desc()).limit(limit)
        return list((await session.scalars(statement)).all())
