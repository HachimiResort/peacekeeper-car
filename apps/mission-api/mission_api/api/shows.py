"""Persistent music-show scores and two-phase vehicle dispatch."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_session
from ..errors import ApiError
from ..models import Robot, Show
from ..schemas import ShowCreate

router = APIRouter(prefix="/api/shows", tags=["shows"])

def view(show: Show): return {"id": str(show.id), "name": show.name, "score": show.score, "created_at": show.created_at, "updated_at": show.updated_at}

@router.get("")
async def list_shows(session: AsyncSession = Depends(get_session)):
    return {"shows": [view(item) for item in (await session.scalars(select(Show).order_by(Show.updated_at.desc()))).all()]}

@router.post("", status_code=201)
async def create_show(payload: ShowCreate, session: AsyncSession = Depends(get_session)):
    _validate(payload.score); item = Show(name=payload.name, score=payload.score); session.add(item); await session.commit(); await session.refresh(item); return view(item)

@router.get("/time/now")
async def server_time(): return {"utc": datetime.now(timezone.utc).isoformat(), "epoch_ms": int(datetime.now(timezone.utc).timestamp() * 1000)}

@router.get("/{show_id}")
async def get_show(show_id: UUID, session: AsyncSession = Depends(get_session)):
    item = await session.get(Show, show_id)
    if not item: raise ApiError(404, "show_not_found", "Show was not found")
    return view(item)

@router.put("/{show_id}")
async def update_show(show_id: UUID, payload: ShowCreate, session: AsyncSession = Depends(get_session)):
    item = await session.get(Show, show_id)
    if not item: raise ApiError(404, "show_not_found", "Show was not found")
    _validate(payload.score); item.name, item.score = payload.name, payload.score; await session.commit(); await session.refresh(item); return view(item)

@router.delete("/{show_id}", status_code=204)
async def delete_show(show_id: UUID, session: AsyncSession = Depends(get_session)):
    item = await session.get(Show, show_id)
    if not item: raise ApiError(404, "show_not_found", "Show was not found")
    await session.delete(item); await session.commit()

@router.post("/{show_id}/start")
async def start_show(show_id: UUID, request: Request, session: AsyncSession = Depends(get_session)):
    show = await session.get(Show, show_id)
    if not show: raise ApiError(404, "show_not_found", "Show was not found")
    _validate(show.score); ids = show.score["robot_ids"]
    robots = {r.id: r for r in (await session.scalars(select(Robot).where(Robot.id.in_(ids), Robot.enabled.is_(True)))).all()}
    if set(robots) != set(ids): raise ApiError(409, "show_robot_unavailable", "A selected robot is disabled or missing")
    plans = {rid: _compile(show.score, rid) for rid in ids}
    async def prepare(rid):
        sent_at = time.time()
        status = await request.app.state.agent_client.request(robots[rid], "POST", "/api/show/prepare", {"show_id": str(show.id), "events": plans[rid]})
        received_at = time.time()
        return {"status": status, "clock_offset_s": _clock_offset(status.get("now_utc"), sent_at, received_at)}
    results = await asyncio.gather(*(prepare(rid) for rid in ids), return_exceptions=True)
    failures = {rid: str(result) for rid, result in zip(ids, results) if isinstance(result, Exception)}
    if failures:
        await asyncio.gather(*(request.app.state.agent_client.request(robots[rid], "POST", "/api/show/abort", {}) for rid in ids), return_exceptions=True)
        raise ApiError(409, "show_prepare_failed", "Not all robots prepared", failures)
    start_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    stamp = start_at.isoformat()
    async def commit(rid, prepared):
        agent_start = start_at + timedelta(seconds=prepared["clock_offset_s"])
        return await request.app.state.agent_client.request(robots[rid], "POST", "/api/show/commit", {"start_at_utc": agent_start.isoformat()})
    commits = await asyncio.gather(*(commit(rid, prepared) for rid, prepared in zip(ids, results)), return_exceptions=True)
    commit_failures = {rid: str(result) for rid, result in zip(ids, commits) if isinstance(result, Exception)}
    if commit_failures:
        await asyncio.gather(*(request.app.state.agent_client.request(robots[rid], "POST", "/api/show/abort", {}) for rid in ids), return_exceptions=True)
        raise ApiError(409, "show_commit_failed", "Not all robots accepted the start time", commit_failures)
    async def monitor():
        while True:
            states = await asyncio.gather(*(request.app.state.agent_client.request(robots[rid], "GET", "/api/show/status") for rid in ids), return_exceptions=True)
            if any(isinstance(item, Exception) or item.get("state") in {"failed", "aborted"} for item in states):
                await asyncio.gather(*(request.app.state.agent_client.request(robots[rid], "POST", "/api/show/abort", {}) for rid in ids), return_exceptions=True)
                return
            if states and all(item.get("state") == "completed" for item in states): return
            await asyncio.sleep(.5)
    request.app.state.show_tasks[str(show.id)] = asyncio.create_task(monitor())
    return {"ok": True, "show_id": str(show.id), "start_at_utc": stamp, "prepared": {rid: result["status"] for rid, result in zip(ids, results)}, "clock_offsets_ms": {rid: round(result["clock_offset_s"] * 1000) for rid, result in zip(ids, results)}}

@router.post("/{show_id}/abort")
async def abort_show(show_id: UUID, request: Request, session: AsyncSession = Depends(get_session)):
    show = await session.get(Show, show_id)
    if not show: raise ApiError(404, "show_not_found", "Show was not found")
    ids = show.score.get("robot_ids", []); robots = (await session.scalars(select(Robot).where(Robot.id.in_(ids)))).all()
    result = await asyncio.gather(*(request.app.state.agent_client.request(r, "POST", "/api/show/abort", {}) for r in robots), return_exceptions=True)
    return {"ok": all(not isinstance(x, Exception) for x in result)}

@router.get("/{show_id}/run-status")
async def run_status(show_id: UUID, request: Request, session: AsyncSession = Depends(get_session)):
    show = await session.get(Show, show_id)
    if not show: raise ApiError(404, "show_not_found", "Show was not found")
    ids = show.score.get("robot_ids", []); robots = (await session.scalars(select(Robot).where(Robot.id.in_(ids)))).all()
    values = await asyncio.gather(*(request.app.state.agent_client.request(r, "GET", "/api/show/status") for r in robots), return_exceptions=True)
    return {"show_id": str(show.id), "robots": {robot.id: value if not isinstance(value, Exception) else {"state": "unreachable", "error": str(value)} for robot, value in zip(robots, values)}}

def _validate(score: dict):
    if score.get("beats_per_bar", 4) != 4 or score.get("ticks_per_beat", 2) != 2: raise ApiError(422, "invalid_score", "v1 requires 4/4 and half-beat grid")
    if not 40 <= float(score.get("bpm", 0)) <= 240 or len(score.get("robot_ids", [])) < 1: raise ApiError(422, "invalid_score", "Score requires BPM 40..240 and at least one robot")
    for rid in score["robot_ids"]:
        for lane in (score.get("tracks", {}).get(rid, {}).get("motion", []), score.get("tracks", {}).get(rid, {}).get("lights", [])):
            last = -1
            for cue in sorted(lane, key=lambda x: x.get("at_tick", 0)):
                if int(cue.get("at_tick", -1)) < last or int(cue.get("duration_ticks", 0)) < 1: raise ApiError(422, "invalid_score", "Cue lanes may not overlap")
                last = int(cue["at_tick"]) + int(cue["duration_ticks"])

def _compile(score: dict, rid: str):
    unit = 60 / float(score["bpm"]) / 2; events = []
    for channel, payload_name in (("motion", "motion"), ("light", "lights")):
        for cue in score["tracks"][rid].get(payload_name, []):
            at = int(cue["at_tick"]) * unit; end = at + int(cue["duration_ticks"]) * unit
            payload = {k: float(cue.get(k, 0)) for k in ("linear_x", "linear_y", "angular_z")} if channel == "motion" else {"effect": cue.get("effect", "off")}
            if channel == "light" and payload["effect"] in {"left_blink", "right_blink", "both_blink", "alternate"}:
                base = payload["effect"].replace("_blink", "")
                for tick in range(int(cue["duration_ticks"])):
                    effect = ("off" if tick % 2 else base) if payload["effect"] != "alternate" else ("left" if tick % 2 == 0 else "right")
                    events.append({"channel": channel, "offset_ms": round((at + tick * unit) * 1000), "payload": {"effect": effect}})
            else:
                events.append({"channel": channel, "offset_ms": round(at * 1000), "payload": payload})
            events.append({"channel": channel, "offset_ms": round(end * 1000), "payload": {"linear_x": 0, "linear_y": 0, "angular_z": 0} if channel == "motion" else {"effect": "off"}})
    return sorted(events, key=lambda x: x["offset_ms"])

def _clock_offset(agent_now_utc, sent_at: float, received_at: float) -> float:
    if not isinstance(agent_now_utc, str): return 0.0
    try: return datetime.fromisoformat(agent_now_utc.replace("Z", "+00:00")).timestamp() - (sent_at + received_at) / 2
    except ValueError: return 0.0
