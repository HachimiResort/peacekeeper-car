import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_robot_or_404, get_session
from ..errors import ApiError
from ..models import MapDeployment
from ..repositories import MapRepository, MissionRepository
from ..schemas import MapDispatchRequest, MapImportRequest, MapListResponse, MapResponse
from ..views import deployment_view, map_view

router = APIRouter(prefix="/api/maps", tags=["maps"])


@router.post("/import-from-robot", status_code=201)
async def import_from_robot(payload: MapImportRequest, request: Request, session: AsyncSession = Depends(get_session)):
    robot = await get_robot_or_404(session, payload.robot_id)
    mission = await MissionRepository.create(session, "map_import", robot.id, payload.model_dump())
    try:
        bundle = await request.app.state.agent_client.export_map(robot, payload.map_name)
        stored, created = await request.app.state.map_service.ingest(
            session,
            bundle,
            source_robot_id=robot.id,
            logical_name_override=payload.logical_name,
        )
        result = {"map": map_view(stored), "created": created}
        await MissionRepository.finish(session, mission, result)
        return {"mission_id": str(mission.id), **result}
    except ValueError as exc:
        await MissionRepository.fail(session, mission, str(exc))
        raise ApiError(400, "invalid_map_bundle", str(exc)) from exc
    except Exception as exc:
        await MissionRepository.fail(session, mission, str(exc))
        raise


@router.post("/upload", status_code=201)
async def upload_map(
    request: Request,
    bundle: UploadFile = File(...),
    logical_name: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    payload = await bundle.read(request.app.state.settings.max_map_bytes + 1)
    if len(payload) > request.app.state.settings.max_map_bytes:
        raise ApiError(413, "map_too_large", "Map bundle exceeds configured size limit")
    mission = await MissionRepository.create(
        session,
        "map_upload",
        None,
        {"filename": bundle.filename, "logical_name": logical_name},
    )
    try:
        stored, created = await request.app.state.map_service.ingest(session, payload, None, logical_name)
        result = {"map": map_view(stored), "created": created}
        await MissionRepository.finish(session, mission, result)
        return {"mission_id": str(mission.id), **result}
    except ValueError as exc:
        await MissionRepository.fail(session, mission, str(exc))
        raise ApiError(400, "invalid_map_bundle", str(exc)) from exc
    except Exception as exc:
        await MissionRepository.fail(session, mission, str(exc))
        raise


@router.get("", response_model=MapListResponse)
async def list_maps(session: AsyncSession = Depends(get_session)):
    return {"maps": [map_view(value) for value in await MapRepository.list(session)]}


async def _get_map(session: AsyncSession, map_id: uuid.UUID):
    value = await MapRepository.get(session, map_id)
    if value is None:
        raise ApiError(404, "map_not_found", "Map was not found")
    return value


@router.get("/{map_id}", response_model=MapResponse)
async def get_map(map_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return map_view(await _get_map(session, map_id))


@router.get("/{map_id}/preview.png")
async def preview_map(map_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)):
    stored = await _get_map(session, map_id)
    png = await request.app.state.map_service.preview(stored)
    return Response(png, media_type="image/png")


@router.get("/{map_id}/download")
async def download_map(map_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)):
    stored = await _get_map(session, map_id)
    payload = await request.app.state.map_service.bundle_for(stored)
    filename = f"{stored.logical_name}__v{stored.version}.zip"
    return Response(payload, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/{map_id}/dispatch")
async def dispatch_map(
    map_id: uuid.UUID,
    payload: MapDispatchRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    stored = await _get_map(session, map_id)
    robots = []
    seen = set()
    for robot_id in payload.robot_ids:
        if robot_id in seen:
            continue
        seen.add(robot_id)
        robot = await get_robot_or_404(session, robot_id)
        if not robot.enabled:
            raise ApiError(409, "robot_disabled", f"Robot '{robot_id}' is disabled")
        robots.append(robot)
    mission = await MissionRepository.create(
        session,
        "map_dispatch",
        None,
        {"map_id": str(map_id), "robot_ids": [robot.id for robot in robots]},
    )
    bundle = await request.app.state.map_service.bundle_for(stored)

    deployments = []
    for robot in robots:
        deployment = MapDeployment(map_id=stored.id, robot_id=robot.id, state="installing", started_at=datetime.now(timezone.utc))
        session.add(deployment)
        deployments.append((robot, deployment))
    await session.commit()

    async def install(robot):
        try:
            result = await request.app.state.agent_client.install_map(robot, bundle)
            return {"ok": True, "result": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    remote_results = await asyncio.gather(*(install(robot) for robot, _ in deployments))
    results = []
    for (_, deployment), remote in zip(deployments, remote_results):
        deployment.finished_at = datetime.now(timezone.utc)
        if remote["ok"]:
            deployment.state = "installed"
            deployment.installed_name = remote["result"].get("name") or remote["result"].get("installed_name")
        else:
            deployment.state = "failed"
            deployment.error = remote["error"]
        results.append({"deployment": deployment_view(deployment), **remote})
    await session.commit()
    response = {"map": map_view(stored), "results": results, "ok": all(item["ok"] for item in results)}
    if response["ok"]:
        await MissionRepository.finish(session, mission, response)
    else:
        mission.result_payload = response
        await MissionRepository.fail(session, mission, "One or more map deployments failed")
    return {"mission_id": str(mission.id), **response}
