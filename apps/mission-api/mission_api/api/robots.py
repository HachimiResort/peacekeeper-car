from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_robot_or_404, get_session
from ..errors import ApiError
from ..models import Robot
from ..repositories import MissionRepository, RobotRepository
from ..schemas import (
    AudioPlayCommand,
    BuzzerControlCommand,
    LightControlCommand,
    ManualCommand,
    MapNameRequest,
    MapSaveRequest,
    HazardHoldRequest,
    HazardMonitorRequest,
    PatrolRequest,
    PoseRequest,
    RobotCreate,
    RobotListResponse,
    RobotResponse,
    RobotUpdate,
    VisionCaptureResponse,
    VisionStatusResponse,
)
from ..views import robot_view

router = APIRouter(prefix="/api/robots", tags=["robots"])


@router.get("", response_model=RobotListResponse)
async def list_robots(request: Request, session: AsyncSession = Depends(get_session)):
    robots = await RobotRepository.list(session)
    cache = request.app.state.status_aggregator.snapshot()
    return {"robots": [robot_view(robot, cache.get(robot.id)) for robot in robots]}


@router.post("", status_code=201, response_model=RobotResponse)
async def create_robot(payload: RobotCreate, request: Request, session: AsyncSession = Depends(get_session)):
    try:
        robot = await RobotRepository.create(
            session,
            id=payload.id,
            name=payload.name,
            base_url=str(payload.base_url).rstrip("/"),
            role=payload.role,
            enabled=payload.enabled,
            capabilities=payload.capabilities,
        )
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "robot_conflict", "Robot ID or base URL already exists") from exc
    return robot_view(robot)


@router.get("/{robot_id}", response_model=RobotResponse)
async def get_robot(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        return robot_view(robot, {"online": False, "error": "robot_disabled", "status": None})
    runtime = request.app.state.status_aggregator.snapshot().get(robot.id)
    return robot_view(robot, runtime)


@router.patch("/{robot_id}", response_model=RobotResponse)
async def update_robot(robot_id: str, payload: RobotUpdate, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    values = payload.model_dump(exclude_unset=True)
    if values.get("base_url") is not None:
        values["base_url"] = str(values["base_url"]).rstrip("/")
    try:
        robot = await RobotRepository.update(session, robot, values)
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "robot_conflict", "Robot base URL already exists") from exc
    return robot_view(robot)


@router.get("/{robot_id}/status", response_model=RobotResponse)
async def robot_status(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        return robot_view(robot, {"online": False, "error": "robot_disabled", "status": None})
    runtime = request.app.state.status_aggregator.snapshot().get(robot.id)
    if runtime is None:
        try:
            status = await request.app.state.agent_client.status(robot)
            runtime = {"online": True, "status": status}
        except Exception as exc:
            runtime = {"online": False, "error": str(exc), "status": None}
    return robot_view(robot, runtime)


async def _proxy(
    request: Request,
    session: AsyncSession,
    robot_id: str,
    path: str,
    payload: Optional[dict] = None,
    mission_type: Optional[str] = None,
) -> dict[str, Any]:
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    mission = None
    if mission_type:
        mission = await MissionRepository.create(session, mission_type, robot.id, payload or {})
    try:
        result = await request.app.state.agent_client.request(robot, "POST", path, payload or {})
    except Exception as exc:
        if mission:
            await MissionRepository.fail(session, mission, str(exc))
        raise
    if mission:
        await MissionRepository.finish(session, mission, result)
        return {"mission_id": str(mission.id), "result": result}
    return result


@router.post("/{robot_id}/control/manual")
async def manual(robot_id: str, payload: ManualCommand, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/control/manual_cmd", payload.model_dump())


@router.post("/{robot_id}/control/stop")
async def stop(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/control/stop")


@router.post("/{robot_id}/control/estop")
async def estop(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/control/estop")


@router.post("/{robot_id}/control/clear-estop")
async def clear_estop(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/system/clear_estop")


@router.post("/{robot_id}/control/lights")
async def control_lights(
    robot_id: str,
    payload: LightControlCommand,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    return await _proxy(request, session, robot_id, "/api/control/lights", payload.model_dump())


@router.post("/{robot_id}/control/buzzer")
async def control_buzzer(
    robot_id: str,
    payload: BuzzerControlCommand,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    return await _proxy(request, session, robot_id, "/api/control/buzzer", payload.model_dump())


@router.get("/{robot_id}/audio/assets")
async def audio_assets(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    return await request.app.state.agent_client.request(robot, "GET", "/api/audio/assets")


@router.get("/{robot_id}/audio/status")
async def audio_status(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    return await request.app.state.agent_client.request(robot, "GET", "/api/audio/status")


@router.post("/{robot_id}/audio/upload")
async def audio_upload(
    robot_id: str,
    request: Request,
    bundle: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    payload = await bundle.read(request.app.state.settings.max_audio_bytes + 1)
    if len(payload) > request.app.state.settings.max_audio_bytes:
        raise ApiError(413, "audio_too_large", "Audio asset exceeds configured size limit")
    mission = await MissionRepository.create(
        session,
        "audio_upload",
        robot.id,
        {"filename": bundle.filename, "bytes": len(payload)},
    )
    try:
        result = await request.app.state.agent_client.install_audio(robot, bundle.filename or "", payload)
        await MissionRepository.finish(session, mission, result)
        return {"mission_id": str(mission.id), "result": result}
    except Exception as exc:
        await MissionRepository.fail(session, mission, str(exc))
        raise


@router.post("/{robot_id}/audio/play")
async def audio_play(
    robot_id: str,
    payload: AudioPlayCommand,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    return await _proxy(request, session, robot_id, "/api/audio/play", payload.model_dump())


@router.post("/{robot_id}/audio/stop")
async def audio_stop(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/audio/stop")


@router.post("/{robot_id}/mapping/start")
async def mapping_start(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/mapping/start", mission_type="mapping")


@router.post("/{robot_id}/mapping/save")
async def mapping_save(robot_id: str, payload: MapSaveRequest, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/mapping/save", payload.model_dump(), "map_save")


@router.post("/{robot_id}/mapping/stop")
async def mapping_stop(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/mapping/stop")


@router.get("/{robot_id}/mapping/live-meta")
async def mapping_live_meta(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    return await request.app.state.agent_client.live_map_status(robot)


@router.get("/{robot_id}/mapping/live.png")
async def mapping_live_preview(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    image = await request.app.state.agent_client.live_map_preview(robot)
    return Response(
        image,
        media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/{robot_id}/video/sample.jpg")
async def video_sample(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    image = await request.app.state.agent_client.video_sample(robot)
    return Response(
        image,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/{robot_id}/video/stream.mjpg")
async def video_stream(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    upstream = await request.app.state.agent_client.open_video_stream(robot)

    async def stream_bytes():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        stream_bytes(),
        media_type=upstream.headers.get("content-type", "multipart/x-mixed-replace; boundary=frame"),
        headers={
            "Cache-Control": "no-store, max-age=0",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{robot_id}/maps/saved")
async def saved_maps(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    return await request.app.state.agent_client.saved_maps(robot)


@router.get("/{robot_id}/maps/saved/preview.png")
async def saved_map_preview(
    robot_id: str,
    request: Request,
    name: str = Query(..., min_length=1, max_length=128),
    session: AsyncSession = Depends(get_session),
):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    image = await request.app.state.agent_client.saved_map_preview(robot, name)
    return Response(
        image,
        media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/{robot_id}/vision/status", response_model=VisionStatusResponse)
async def vision_status(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    return await request.app.state.agent_client.vision_status(robot)


@router.post("/{robot_id}/vision/capture", response_model=VisionCaptureResponse)
async def vision_capture(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    return await request.app.state.agent_client.vision_capture(robot)


@router.get("/{robot_id}/vision/latest", response_model=VisionCaptureResponse)
async def vision_latest(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    return await request.app.state.agent_client.vision_latest(robot)


@router.get("/{robot_id}/vision/latest.jpg")
async def vision_latest_image(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    image = await request.app.state.agent_client.vision_latest_image(robot)
    return Response(
        image,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/{robot_id}/vision/stream.mjpg")
async def vision_stream(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    upstream = await request.app.state.agent_client.open_vision_stream(robot)

    async def stream_bytes():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        stream_bytes(),
        media_type=upstream.headers.get("content-type", "multipart/x-mixed-replace; boundary=frame"),
        headers={
            "Cache-Control": "no-store, max-age=0",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{robot_id}/hazards/status")
async def hazard_status(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, robot_id)
    if not robot.enabled:
        raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
    return await request.app.state.agent_client.request(robot, "GET", "/api/hazards/status")


@router.post("/{robot_id}/hazards/monitor")
async def hazard_monitor(robot_id: str, payload: HazardMonitorRequest, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/hazards/monitor", payload.model_dump())


@router.post("/{robot_id}/hazards/{event_key}/takeover")
async def hazard_takeover(robot_id: str, event_key: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, f"/api/hazards/{event_key}/takeover")


@router.post("/{robot_id}/hazards/{event_key}/hold")
async def hazard_hold(robot_id: str, event_key: str, payload: HazardHoldRequest, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, f"/api/hazards/{event_key}/hold", payload.model_dump())


@router.post("/{robot_id}/hazards/{event_key}/resume")
async def hazard_resume(robot_id: str, event_key: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, f"/api/hazards/{event_key}/resume")


@router.post("/{robot_id}/navigation/start")
async def navigation_start(robot_id: str, payload: MapNameRequest, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/navigation/start", payload.model_dump(), "navigation_start")


@router.post("/{robot_id}/navigation/initial-pose")
async def initial_pose(robot_id: str, payload: PoseRequest, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/navigation/initial_pose", payload.model_dump())


@router.post("/{robot_id}/navigation/goal")
async def navigation_goal(robot_id: str, payload: PoseRequest, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/navigation/goal", payload.model_dump(), "navigation_goal")


@router.post("/{robot_id}/navigation/cancel")
async def navigation_cancel(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/navigation/cancel")


@router.post("/{robot_id}/navigation/stop")
async def navigation_stop(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/navigation/stop")


@router.post("/{robot_id}/patrol/start")
async def patrol_start(robot_id: str, payload: PatrolRequest, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/patrol/start", payload.model_dump(), "patrol")


@router.post("/{robot_id}/patrol/pause")
async def patrol_pause(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/patrol/pause")


@router.post("/{robot_id}/patrol/resume")
async def patrol_resume(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/patrol/resume")


@router.post("/{robot_id}/patrol/cancel")
async def patrol_cancel(robot_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    return await _proxy(request, session, robot_id, "/api/patrol/cancel")
