import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .agent_client import FleetAgentClient
from .api.maps import router as maps_router
from .api.operations import router as operations_router
from .api.robots import router as robots_router
from .api.shows import router as shows_router
from .config import Settings, get_settings
from .db import Database
from .errors import ApiError
from .map_service import MapService
from .repositories import MissionRepository
from .runtime import StatusAggregator, WebSocketHub
from .security import verify_http_token, verify_websocket_token
from .seed import seed_robots
from .schemas import ErrorResponse
from .voice import ArkResponsesClient, ConversationOrchestrator, RobotToolExecutor, VolcASRClient, VolcTTSClient
from .voice.gateway import VoiceConnectionRegistry, voice_websocket

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.map_storage_dir.mkdir(parents=True, exist_ok=True)
        settings.evidence_storage_dir.mkdir(parents=True, exist_ok=True)
        db = Database(settings.database_url)
        agent = FleetAgentClient(
            settings.shared_token,
            settings.status_timeout_s,
            settings.control_timeout_s,
            settings.map_timeout_s,
        )
        hub = WebSocketHub()
        aggregator = StatusAggregator(
            db.sessions,
            agent,
            hub,
            settings.status_poll_s,
            settings.offline_failures,
            settings.last_seen_flush_s,
            settings.max_poll_concurrency,
        )
        app.state.settings = settings
        app.state.db = db
        app.state.agent_client = agent
        app.state.ws_hub = hub
        app.state.status_aggregator = aggregator
        app.state.map_service = MapService(settings.map_storage_dir, settings.max_map_bytes)
        app.state.show_tasks = {}
        app.state.voice_connections = VoiceConnectionRegistry()
        app.state.voice_asr = None
        app.state.voice_tts = None
        app.state.voice_ark = None
        app.state.conversation_orchestrator = None
        if settings.voice_configured:
            app.state.voice_asr = VolcASRClient(
                settings.volc_speech_app_id,
                settings.volc_speech_access_token,
                settings.volc_asr_resource_id,
                settings.volc_asr_url,
                settings.voice_provider_timeout_s,
            )
            app.state.voice_tts = VolcTTSClient(
                settings.volc_speech_app_id,
                settings.volc_speech_access_token,
                settings.volc_tts_resource_id,
                settings.volc_tts_voice,
                settings.volc_tts_url,
                settings.voice_provider_timeout_s,
            )
            app.state.voice_ark = ArkResponsesClient(
                settings.ark_api_key,
                settings.ark_model,
                settings.ark_base_url,
                settings.voice_provider_timeout_s,
            )
            app.state.conversation_orchestrator = ConversationOrchestrator(
                app.state.voice_ark,
                RobotToolExecutor(agent, settings.voice_max_image_bytes),
                settings.voice_max_tool_rounds,
            )
        async with db.sessions() as session:
            await seed_robots(session, settings.cars_file)
            await MissionRepository.reconcile_interrupted(session)
        aggregator.start()
        try:
            yield
        finally:
            for task in app.state.show_tasks.values(): task.cancel()
            await aggregator.stop()
            if app.state.voice_ark is not None:
                await app.state.voice_ark.close()
            if app.state.voice_tts is not None:
                await app.state.voice_tts.close()
            await agent.close()
            await db.close()

    app = FastAPI(
        title="Peacekeeper Mission API",
        version="0.1.0",
        lifespan=lifespan,
        responses={
            401: {"model": ErrorResponse, "description": "Missing or invalid shared token"},
            409: {"model": ErrorResponse, "description": "Operation conflicts with current state"},
            422: {"model": ErrorResponse, "description": "Request validation failed"},
            500: {"model": ErrorResponse, "description": "Unexpected service error"},
        },
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        if request.url.path.startswith(("/api/", "/internal/")) or request.url.path in {"/api", "/internal"}:
            try:
                verify_http_token(request, settings.shared_token)
            except ApiError as exc:
                return _error_response(exc, request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return _error_response(exc, getattr(request.state, "request_id", uuid.uuid4().hex))

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError):
        del exc
        return _error_response(
            ApiError(409, "database_conflict", "Database uniqueness constraint was violated"),
            getattr(request.state, "request_id", uuid.uuid4().hex),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        details = [
            {
                "location": list(item.get("loc", [])),
                "message": item.get("msg"),
                "type": item.get("type"),
            }
            for item in exc.errors()
        ]
        return _error_response(
            ApiError(422, "validation_error", "Request validation failed", details),
            getattr(request.state, "request_id", uuid.uuid4().hex),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        return _error_response(
            ApiError(exc.status_code, "http_error", str(exc.detail)),
            getattr(request.state, "request_id", uuid.uuid4().hex),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled mission-api request error")
        return _error_response(
            ApiError(500, "internal_error", "Unexpected mission-api error", {"type": type(exc).__name__}),
            getattr(request.state, "request_id", uuid.uuid4().hex),
        )

    @app.get("/health/live")
    async def health_live():
        return {"ok": True}

    @app.get("/health/ready")
    async def health_ready(request: Request):
        ready = await request.app.state.db.ready() and request.app.state.map_service.ready()
        return JSONResponse({"ok": ready}, status_code=200 if ready else 503)

    @app.websocket("/ws/status")
    async def status_websocket(websocket: WebSocket):
        if not await verify_websocket_token(websocket, settings.shared_token):
            return
        await websocket.accept()
        queue = await websocket.app.state.ws_hub.subscribe()
        try:
            await websocket.send_json({"type": "snapshot", "robots": websocket.app.state.status_aggregator.snapshot()})
            while True:
                message = await queue.get()
                await websocket.send_json(message)
        except WebSocketDisconnect:
            pass
        finally:
            await websocket.app.state.ws_hub.unsubscribe(queue)

    @app.websocket("/ws/voice/{robot_id}")
    async def robot_voice_websocket(websocket: WebSocket, robot_id: str):
        await voice_websocket(websocket, robot_id)

    app.include_router(robots_router)
    app.include_router(operations_router)
    app.include_router(maps_router)
    app.include_router(shows_router)
    return app


def _error_response(exc: ApiError, request_id: str) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": request_id,
            }
        },
        status_code=exc.status_code,
        headers={"X-Request-ID": request_id},
    )


app = create_app()
