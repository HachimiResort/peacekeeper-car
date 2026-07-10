import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_session
from ..errors import ApiError
from ..models import Alert, Event, MapDeployment, Mission, Robot, StoredMap
from ..repositories import RobotRepository
from ..schemas import AlertConfirmRequest, AlertListResponse, DeploymentListResponse, EventListResponse, MissionListResponse, MissionResponse, OverviewResponse, RobotEventRequest
from ..views import alert_view, deployment_view, event_view, mission_view

router = APIRouter(tags=["operations"])


@router.get("/api/overview", response_model=OverviewResponse)
async def overview(request: Request, session: AsyncSession = Depends(get_session)):
    robot_total = int(await session.scalar(select(func.count()).select_from(Robot)) or 0)
    robot_enabled = int(
        await session.scalar(select(func.count()).select_from(Robot).where(Robot.enabled.is_(True))) or 0
    )
    cache = request.app.state.status_aggregator.snapshot()
    online = sum(1 for value in cache.values() if value.get("online"))
    running = int(
        await session.scalar(
            select(func.count()).select_from(Mission).where(Mission.state.in_(["pending", "running"]))
        )
        or 0
    )
    failed = int(
        await session.scalar(select(func.count()).select_from(Mission).where(Mission.state == "failed")) or 0
    )
    mission_total = int(await session.scalar(select(func.count()).select_from(Mission)) or 0)
    pending_alerts = int(
        await session.scalar(select(func.count()).select_from(Alert).where(Alert.state == "pending")) or 0
    )
    alert_total = int(await session.scalar(select(func.count()).select_from(Alert)) or 0)
    map_total = int(await session.scalar(select(func.count()).select_from(StoredMap)) or 0)
    latest_map = await session.scalar(select(func.max(StoredMap.created_at)))
    return {
        "robots": {"total": robot_total, "enabled": robot_enabled, "online": online, "offline": max(robot_enabled - online, 0)},
        "missions": {"total": mission_total, "running": running, "failed": failed},
        "alerts": {"total": alert_total, "pending": pending_alerts},
        "maps": {"total": map_total, "latest_created_at": latest_map.isoformat() if latest_map else None},
    }


@router.get("/api/missions", response_model=MissionListResponse)
async def list_missions(
    robot_id: str | None = None,
    state: str | None = None,
    mission_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    filters = []
    if robot_id:
        filters.append(Mission.robot_id == robot_id)
    if state:
        filters.append(Mission.state == state)
    if mission_type:
        filters.append(Mission.mission_type == mission_type)
    total = int(await session.scalar(select(func.count()).select_from(Mission).where(*filters)) or 0)
    statement = select(Mission).where(*filters).order_by(Mission.created_at.desc()).limit(limit).offset(offset)
    values = list((await session.scalars(statement)).all())
    return {"missions": [mission_view(value) for value in values], "total": total, "limit": limit, "offset": offset}


@router.get("/api/missions/{mission_id}", response_model=MissionResponse)
async def get_mission(mission_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    value = await session.get(Mission, mission_id)
    if value is None:
        raise ApiError(404, "mission_not_found", "Mission was not found")
    return mission_view(value)


@router.get("/api/events", response_model=EventListResponse)
async def list_events(
    robot_id: str | None = None,
    severity: str | None = None,
    event_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    filters = []
    if robot_id:
        filters.append(Event.robot_id == robot_id)
    if severity:
        filters.append(Event.severity == severity)
    if event_type:
        filters.append(Event.event_type == event_type)
    total = int(await session.scalar(select(func.count()).select_from(Event).where(*filters)) or 0)
    statement = select(Event).where(*filters).order_by(Event.received_at.desc()).limit(limit).offset(offset)
    values = list((await session.scalars(statement)).all())
    return {"events": [event_view(value) for value in values], "total": total, "limit": limit, "offset": offset}


@router.get("/api/alerts", response_model=AlertListResponse)
async def list_alerts(
    state: str | None = None,
    robot_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    filters = []
    if state:
        filters.append(Alert.state == state)
    if robot_id:
        filters.append(Event.robot_id == robot_id)
    base = select(Alert, Event).join(Event, Alert.event_id == Event.id).where(*filters)
    total = int(
        await session.scalar(
            select(func.count()).select_from(Alert).join(Event, Alert.event_id == Event.id).where(*filters)
        )
        or 0
    )
    rows = (await session.execute(base.order_by(Event.received_at.desc()).limit(limit).offset(offset))).all()
    alerts = [{**alert_view(alert), "event": event_view(event)} for alert, event in rows]
    return {"alerts": alerts, "total": total, "limit": limit, "offset": offset}


@router.get("/api/map-deployments", response_model=DeploymentListResponse)
async def list_map_deployments(
    map_id: uuid.UUID | None = None,
    robot_id: str | None = None,
    state: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    filters = []
    if map_id:
        filters.append(MapDeployment.map_id == map_id)
    if robot_id:
        filters.append(MapDeployment.robot_id == robot_id)
    if state:
        filters.append(MapDeployment.state == state)
    joined = (
        select(MapDeployment, StoredMap, Robot)
        .join(StoredMap, MapDeployment.map_id == StoredMap.id)
        .join(Robot, MapDeployment.robot_id == Robot.id)
        .where(*filters)
    )
    total = int(await session.scalar(select(func.count()).select_from(MapDeployment).where(*filters)) or 0)
    rows = (await session.execute(joined.order_by(MapDeployment.created_at.desc()).limit(limit).offset(offset))).all()
    deployments = []
    for deployment, stored_map, robot in rows:
        deployments.append(
            {
                **deployment_view(deployment),
                "map": {"id": str(stored_map.id), "logical_name": stored_map.logical_name, "version": stored_map.version},
                "robot": {"id": robot.id, "name": robot.name},
            }
        )
    return {"deployments": deployments, "total": total, "limit": limit, "offset": offset}


@router.post("/internal/robot-events", status_code=201)
async def create_event(payload: RobotEventRequest, session: AsyncSession = Depends(get_session)):
    if await session.get(Robot, payload.robot_id) is None:
        raise ApiError(404, "robot_not_found", f"Robot '{payload.robot_id}' was not found")
    value = Event(
        robot_id=payload.robot_id,
        mission_id=payload.mission_id,
        event_type=payload.event_type,
        severity=payload.severity,
        payload=payload.payload,
        occurred_at=payload.occurred_at,
    )
    session.add(value)
    await session.flush()
    alert = None
    if payload.create_alert:
        alert = Alert(event_id=value.id, state="pending")
        session.add(alert)
    await session.commit()
    await session.refresh(value)
    result = {"event": event_view(value)}
    if alert is not None:
        await session.refresh(alert)
        result["alert"] = alert_view(alert)
    return result


@router.post("/api/alerts/{alert_id}/confirm")
async def confirm_alert(alert_id: uuid.UUID, payload: AlertConfirmRequest, session: AsyncSession = Depends(get_session)):
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise ApiError(404, "alert_not_found", "Alert was not found")
    if alert.state != "pending":
        raise ApiError(409, "alert_already_handled", f"Alert is already {alert.state}")
    alert.state = "confirmed"
    alert.confirmed_by = payload.confirmed_by
    alert.confirmed_at = datetime.now(timezone.utc)
    alert.resolution = payload.resolution
    await session.commit()
    return alert_view(alert)


@router.post("/api/fleet/stop")
async def fleet_stop(request: Request, session: AsyncSession = Depends(get_session)):
    robots = await RobotRepository.list(session, enabled_only=True)

    async def stop_one(robot: Robot):
        try:
            result = await request.app.state.agent_client.request(robot, "POST", "/api/control/stop", {})
            return {"robot_id": robot.id, "ok": True, "result": result}
        except Exception as exc:
            return {"robot_id": robot.id, "ok": False, "error": str(exc)}

    results = await asyncio.gather(*(stop_one(robot) for robot in robots))
    return {"ok": all(item["ok"] for item in results), "results": results}
