"""FastAPI application for the vehicle-side fleet-agent."""
from __future__ import annotations

import asyncio
import json
from functools import partial
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from .command_arbiter import CommandArbiter
from .config import AgentConfig
from .mapping import MappingService
from .navigation import NavigationService
from .process_manager import ProcessManager
from .ros_control import DirectCmdVelSubscriber, DirectOdomPublisher
from .rosmaster_control import RosmasterController
from .schemas import CmdVelRequest, NavigationPoseRequest, NavigationStartRequest, ProcessRequest, SaveMapRequest
from .state import Mode, RuntimeState
from .video import VideoService


async def _run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    call = partial(func, *args, **kwargs)
    return await loop.run_in_executor(None, call)


def create_app(config: AgentConfig) -> FastAPI:
    state = RuntimeState()
    data_dir = Path(config.data_dir)
    run_dir = data_dir / "runs" / state.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "maps").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)

    rosmaster = RosmasterController(config.control, config.safety)
    direct_odom = DirectOdomPublisher(
        config.ros.odom_topic,
        config.ros.odom_frame,
        config.ros.base_frame,
        config.safety,
        setup_paths=(config.ros.distro_setup, config.ros.workspace_setup),
        feedback_source=rosmaster,
        publish_odom=config.ros.publish_odom,
        publish_tf=config.ros.publish_tf,
        period_s=config.ros.odom_period_s,
        motion_deadband=config.control.direct_deadband,
        base_link_frame=config.ros.base_link_frame,
        linear_x_scale=config.ros.odom_linear_x_scale,
        linear_y_scale=config.ros.odom_linear_y_scale,
        angular_z_scale=config.ros.odom_angular_z_scale,
    )
    process_manager = ProcessManager(config)
    cmd_vel = CommandArbiter(
        rosmaster,
        state,
        config.safety,
        manual_override_s=config.control.manual_override_s,
        motion_sink=direct_odom,
    )
    direct_cmd_vel = DirectCmdVelSubscriber(
        config.ros.cmd_vel_topic,
        cmd_vel,
        setup_paths=(config.ros.distro_setup, config.ros.workspace_setup),
    )
    video = VideoService(config.video, run_dir)
    mapping = MappingService(config, process_manager)
    navigation = NavigationService(
        config,
        process_manager,
        setup_paths=(config.ros.distro_setup, config.ros.workspace_setup),
    )

    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            process_manager.start_auto_processes()
        except Exception as exc:
            state.set_error(f"Auto-start failed: {exc}")
        try:
            direct_odom.start()
        except Exception as exc:
            state.set_error(f"Direct odom bridge failed: {exc}")
        if direct_cmd_vel is not None:
            try:
                direct_cmd_vel.start()
            except Exception as exc:
                state.set_error(f"Direct /cmd_vel subscriber failed: {exc}")
        try:
            yield
        finally:
            navigation.shutdown()
            direct_odom.shutdown()
            if direct_cmd_vel is not None:
                direct_cmd_vel.shutdown()
            cmd_vel.shutdown()
            video.stop()
            process_manager.stop_all()

    app = FastAPI(
        title="Peacekeeper Fleet Agent",
        description="Vehicle-side FPV control, ROS2 process management, and mapping API",
        version="0.1.0",
        lifespan=lifespan,
    )

    def build_status() -> dict:
        process_status = process_manager.status()
        ros_status = cmd_vel.status()
        ros_status["direct_odom_bridge"] = direct_odom.status()
        if direct_cmd_vel is not None:
            ros_status["direct_cmd_vel_subscriber"] = direct_cmd_vel.status()
        ros_status.update(
            {
                "odom_active": process_manager.topic_active(config.ros.odom_topic),
                "scan_active": process_manager.topic_active(config.ros.scan_topic),
                "map_active": process_manager.topic_active(config.ros.map_topic),
            }
        )
        return {
            "run_id": state.run_id,
            "mode": state.mode.value,
            "processes": {
                key: value["status"] for key, value in process_status.items()
            },
            "process_details": process_status,
            "video": video.status(),
            "navigation": navigation.status(),
            "ros": ros_status,
            "last_error": state.last_error,
            "data_dir": str(data_dir),
            "run_dir": str(run_dir),
        }

    def is_zero_cmd(payload: CmdVelRequest) -> bool:
        return (
            abs(payload.linear_x) < 1e-9
            and abs(payload.linear_y) < 1e-9
            and abs(payload.angular_z) < 1e-9
        )

    async def publish_manual_command(payload: CmdVelRequest):
        if state.mode == Mode.LASER_TRACKING and is_zero_cmd(payload):
            return await stop_laser_tracking()
        result = await _run_blocking(
            cmd_vel.handle_manual_command,
            payload.linear_x,
            payload.linear_y,
            payload.angular_z,
            payload.ttl_ms,
        )
        if not result["ok"]:
            return JSONResponse(result, status_code=409)
        return {"ok": True, "mode": state.mode.value, "source": payload.source}

    async def stop_laser_tracking():
        try:
            await _run_blocking(cmd_vel.stop)
            if "laser_tracker" in config.processes:
                await _run_blocking(process_manager.stop, "laser_tracker")
            return {"ok": True, "mode": state.mode.value}
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=503)

    @app.get("/")
    async def index(request: Request):
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "agent_port": config.port,
                "run_id": state.run_id,
            },
        )

    @app.get("/api/status")
    async def api_status():
        return JSONResponse(await _run_blocking(build_status))

    @app.websocket("/ws")
    async def websocket_status(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                status = await _run_blocking(build_status)
                await websocket.send_text(json.dumps({"type": "status", "data": status}))
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            return

    @app.get("/video.mjpg")
    async def video_mjpg():
        return StreamingResponse(
            video.mjpeg_frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/api/video/sample.jpg")
    async def video_sample_jpg():
        frame = await _run_blocking(video.read_jpeg)
        return Response(frame, media_type="image/jpeg")

    @app.get("/api/mapping/latest")
    async def mapping_latest(name: Optional[str] = Query(default=None)):
        try:
            info = await _run_blocking(mapping.latest_map, name)
            return JSONResponse({"ok": True, **info})
        except FileNotFoundError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

    @app.get("/api/mapping/preview.png")
    async def mapping_preview_png(name: Optional[str] = Query(default=None)):
        try:
            image = await _run_blocking(mapping.render_map_png, name)
            return Response(image, media_type="image/png")
        except FileNotFoundError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

    @app.get("/api/mapping/meta")
    async def mapping_meta(name: Optional[str] = Query(default=None)):
        try:
            info = await _run_blocking(mapping.map_meta, name)
            return JSONResponse({"ok": True, **info})
        except FileNotFoundError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

    @app.post("/api/control/cmd_vel")
    async def control_cmd_vel(payload: CmdVelRequest):
        return await publish_manual_command(payload)

    @app.post("/api/control/manual_cmd")
    async def control_manual_cmd(payload: CmdVelRequest):
        return await publish_manual_command(payload)

    @app.post("/api/control/stop")
    async def control_stop():
        if state.mode == Mode.LASER_TRACKING:
            return await stop_laser_tracking()
        try:
            if state.mode == Mode.NAV_PATROL:
                await _run_blocking(navigation.cancel_goal)
            await _run_blocking(cmd_vel.stop)
            if state.mode == Mode.MANUAL:
                state.set_mode(Mode.IDLE)
            return {"ok": True, "mode": state.mode.value}
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=503)

    @app.post("/api/control/estop")
    async def control_estop(stop_processes: bool = Query(default=True)):
        state.set_estop()
        try:
            await _run_blocking(navigation.cancel_goal)
        except Exception:
            pass
        try:
            await _run_blocking(cmd_vel.stop)
        except Exception:
            pass
        await _run_blocking(video.stop_recording)
        stopped = None
        if stop_processes:
            stopped = await _run_blocking(process_manager.stop_all)
        return {"ok": True, "mode": state.mode.value, "processes": stopped}

    @app.post("/api/process/start")
    async def process_start(payload: ProcessRequest):
        if state.mode == Mode.LASER_TRACKING:
            return JSONResponse(
                {"ok": False, "message": "Laser tracking is active; stop tracking before starting processes"},
                status_code=409,
            )
        try:
            status = await _run_blocking(process_manager.start, payload.process)
            return {"ok": True, "process": payload.process, "status": status}
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

    @app.post("/api/process/stop")
    async def process_stop(payload: ProcessRequest):
        if state.mode == Mode.LASER_TRACKING:
            return JSONResponse(
                {"ok": False, "message": "Laser tracking is active; use tracking stop or stop_all"},
                status_code=409,
            )
        try:
            status = await _run_blocking(process_manager.stop, payload.process)
            return {"ok": True, "process": payload.process, "status": status}
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

    @app.post("/api/process/stop_all")
    async def process_stop_all():
        try:
            await _run_blocking(navigation.cancel_goal)
            await _run_blocking(cmd_vel.stop)
        except Exception:
            pass
        processes = await _run_blocking(process_manager.stop_all)
        if state.mode in (Mode.LASER_TRACKING, Mode.NAV_PATROL, Mode.MAPPING, Mode.SAVING_MAP):
            state.set_mode(Mode.IDLE)
        return {"ok": True, "processes": processes}

    @app.post("/api/mapping/start")
    async def mapping_start():
        if state.estop:
            return JSONResponse({"ok": False, "message": "ESTOP is active"}, status_code=409)
        if state.mode == Mode.LASER_TRACKING:
            return JSONResponse(
                {"ok": False, "message": "Laser tracking is active; stop tracking before mapping"},
                status_code=409,
            )
        try:
            await _run_blocking(process_manager.start, "lidar")
            await _run_blocking(process_manager.start, "slam")
            state.set_mode(Mode.MAPPING)
            processes = await _run_blocking(process_manager.status)
            return {"ok": True, "mode": state.mode.value, "processes": processes}
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

    @app.post("/api/tracking/laser/start")
    async def laser_tracking_start():
        if state.estop:
            return JSONResponse({"ok": False, "message": "ESTOP is active"}, status_code=409)
        if state.mode == Mode.LASER_TRACKING:
            return {"ok": True, "mode": state.mode.value}
        if state.mode not in (Mode.IDLE, Mode.MANUAL):
            return JSONResponse(
                {"ok": False, "message": f"Cannot start laser tracking while mode={state.mode.value}"},
                status_code=409,
            )
        if "laser_tracker" not in config.processes:
            return JSONResponse({"ok": False, "message": "laser_tracker process is not configured"}, status_code=500)
        try:
            await _run_blocking(cmd_vel.stop)
            await _run_blocking(direct_cmd_vel.start)
            await _run_blocking(process_manager.start, "lidar")
            result = await _run_blocking(cmd_vel.enable_ros_cmd_vel, Mode.LASER_TRACKING)
            if not result["ok"]:
                return JSONResponse(result, status_code=409)
            await _run_blocking(process_manager.start, "laser_tracker")
            processes = await _run_blocking(process_manager.status)
            return {"ok": True, "mode": state.mode.value, "processes": processes}
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

    @app.post("/api/tracking/laser/stop")
    async def laser_tracking_stop():
        return await stop_laser_tracking()

    @app.post("/api/control/ros_cmd_vel/start")
    async def ros_cmd_vel_start():
        try:
            await _run_blocking(direct_cmd_vel.start)
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=503)
        result = await _run_blocking(cmd_vel.enable_ros_cmd_vel, Mode.NAV_PATROL)
        if not result["ok"]:
            return JSONResponse(result, status_code=409)
        return {"ok": True, "mode": state.mode.value}

    @app.post("/api/control/ros_cmd_vel/stop")
    async def ros_cmd_vel_stop():
        await _run_blocking(cmd_vel.stop)
        return {"ok": True, "mode": state.mode.value}

    @app.post("/api/navigation/start")
    async def navigation_start(payload: NavigationStartRequest):
        if state.estop:
            return JSONResponse({"ok": False, "message": "ESTOP is active"}, status_code=409)
        if state.mode == Mode.LASER_TRACKING:
            return JSONResponse(
                {"ok": False, "message": "Laser tracking is active; stop tracking before navigation"},
                status_code=409,
            )
        if state.mode in (Mode.MAPPING, Mode.SAVING_MAP):
            return JSONResponse(
                {"ok": False, "message": "Stop mapping before navigation"},
                status_code=409,
            )
        process_status = await _run_blocking(process_manager.status)
        slam_status = process_status.get("slam", {}).get("status")
        if slam_status == "running":
            return JSONResponse(
                {"ok": False, "message": "SLAM is running; stop mapping before navigation"},
                status_code=409,
            )
        try:
            await _run_blocking(process_manager.start, "lidar")
            if direct_cmd_vel is not None:
                await _run_blocking(direct_cmd_vel.start)
            status = await _run_blocking(navigation.start, payload.map_name)
            return {"ok": True, "navigation": status}
        except FileNotFoundError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

    @app.post("/api/navigation/initial_pose")
    async def navigation_initial_pose(payload: NavigationPoseRequest):
        try:
            result = await _run_blocking(navigation.publish_initial_pose, payload.x, payload.y, payload.yaw)
            return result
        except RuntimeError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=409)
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

    @app.post("/api/navigation/goal")
    async def navigation_goal(payload: NavigationPoseRequest):
        if state.estop:
            return JSONResponse({"ok": False, "message": "ESTOP is active"}, status_code=409)
        try:
            if direct_cmd_vel is not None:
                await _run_blocking(direct_cmd_vel.start)
            result = await _run_blocking(cmd_vel.enable_ros_cmd_vel, Mode.NAV_PATROL)
            if not result["ok"]:
                return JSONResponse(result, status_code=409)
            goal = await _run_blocking(navigation.send_goal, payload.x, payload.y, payload.yaw)
            return {"ok": True, "mode": state.mode.value, "navigation": goal}
        except RuntimeError as exc:
            await _run_blocking(cmd_vel.stop)
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=409)
        except Exception as exc:
            await _run_blocking(cmd_vel.stop)
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

    @app.post("/api/navigation/cancel")
    async def navigation_cancel():
        result = await _run_blocking(navigation.cancel_goal)
        await _run_blocking(cmd_vel.stop)
        status_code = 200 if result.get("ok", False) else 500
        return JSONResponse({"ok": result.get("ok", False), "mode": state.mode.value, "navigation": result}, status_code=status_code)

    @app.post("/api/navigation/stop")
    async def navigation_stop():
        result = await _run_blocking(navigation.stop)
        await _run_blocking(cmd_vel.stop)
        return {"ok": True, "mode": state.mode.value, "navigation": result}

    @app.get("/api/navigation/status")
    async def navigation_status():
        status = await _run_blocking(navigation.status)
        return JSONResponse({"ok": True, **status})

    @app.post("/api/mapping/save")
    async def mapping_save(payload: SaveMapRequest):
        map_active = await _run_blocking(process_manager.topic_active, config.ros.map_topic)
        if not map_active:
            return JSONResponse(
                {
                    "ok": False,
                    "message": f"{config.ros.map_topic} is not active; start mapping and verify lidar, SLAM, and direct odom/tf are publishing first",
                },
                status_code=409,
            )
        previous_mode = state.mode
        state.set_mode(Mode.SAVING_MAP)
        try:
            result = await _run_blocking(mapping.save_map, payload.name)
            state.set_mode(previous_mode if previous_mode != Mode.SAVING_MAP else Mode.MAPPING)
            status_code = 200 if result["ok"] else 500
            return JSONResponse(result, status_code=status_code)
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

    @app.post("/api/mapping/stop")
    async def mapping_stop():
        try:
            await _run_blocking(process_manager.stop, "slam")
            if state.mode in (Mode.MAPPING, Mode.SAVING_MAP):
                state.set_mode(Mode.IDLE)
            processes = await _run_blocking(process_manager.status)
            return {"ok": True, "mode": state.mode.value, "processes": processes}
        except Exception as exc:
            state.set_error(str(exc))
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

    @app.post("/api/video/start_record")
    async def video_start_record():
        return await _run_blocking(video.start_recording)

    @app.post("/api/video/stop_record")
    async def video_stop_record():
        return await _run_blocking(video.stop_recording)

    @app.post("/api/video/snapshot")
    async def video_snapshot():
        return await _run_blocking(video.snapshot)

    @app.post("/api/system/clear_estop")
    async def clear_estop():
        state.clear_estop()
        return {"ok": True, "mode": state.mode.value}

    return app
