"""Authenticated car-to-mission voice WebSocket gateway."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..models import Robot
from ..repositories import ConversationRepository
from ..security import verify_websocket_token
from .providers import VoiceProviderError


class VoiceConnectionRegistry:
    def __init__(self):
        self.active: set[str] = set()

    def acquire(self, robot_id: str) -> bool:
        if robot_id in self.active:
            return False
        self.active.add(robot_id)
        return True

    def release(self, robot_id: str) -> None:
        self.active.discard(robot_id)


async def voice_websocket(websocket: WebSocket, robot_id: str) -> None:
    settings = websocket.app.state.settings
    if not await verify_websocket_token(websocket, settings.shared_token):
        return
    registry: VoiceConnectionRegistry = websocket.app.state.voice_connections
    if not registry.acquire(robot_id):
        await websocket.close(code=4409, reason="voice connection already active")
        return
    conversation = None
    robot = None
    try:
        async with websocket.app.state.db.sessions() as db:
            robot = await db.get(Robot, robot_id)
            if robot is None or not robot.enabled:
                await websocket.close(code=4404, reason="robot not available")
                return
            await websocket.accept()
            if websocket.app.state.conversation_orchestrator is None:
                await websocket.send_json({"type": "error", "code": "voice_not_configured", "message": "Volcengine voice credentials are not configured"})
                await websocket.close(code=1013, reason="voice provider not configured")
                return
            conversation = await ConversationRepository.create_session(db, robot_id)
            await websocket.send_json({
                "type": "ready",
                "session_id": str(conversation.id),
                "input_format": "pcm_s16le",
                "input_sample_rate": 16000,
                "output_format": "pcm_s16le",
                "output_sample_rate": 24000,
            })
            history: list[dict[str, Any]] = []
            audio = bytearray()
            turn_open = False
            turns_started = 0
            while True:
                packet = await websocket.receive()
                if packet.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(packet.get("code", 1000))
                raw = packet.get("bytes")
                if raw is not None:
                    if not turn_open:
                        await websocket.send_json({"type": "error", "code": "turn_not_open", "message": "Send turn.start before audio"})
                        continue
                    audio.extend(raw)
                    if len(audio) > 16000 * 2 * 15:
                        turn_open = False
                        audio.clear()
                        await websocket.send_json({"type": "error", "code": "utterance_too_long", "message": "Utterance exceeds 15 seconds"})
                    continue
                message = json.loads(packet.get("text") or "{}")
                message_type = message.get("type")
                if message_type == "hello":
                    await websocket.send_json({"type": "state", "state": "wake_listening"})
                elif message_type == "turn.start":
                    if turn_open:
                        await websocket.send_json({"type": "error", "code": "turn_already_open", "message": "A turn is already open"})
                        continue
                    if bool(message.get("new_session")) and turns_started:
                        await ConversationRepository.close_session(db, conversation)
                        conversation = await ConversationRepository.create_session(db, robot_id)
                        history = []
                    audio.clear()
                    turn_open = True
                    turns_started += 1
                    await websocket.send_json({"type": "state", "state": "listening"})
                elif message_type == "turn.end":
                    if not turn_open:
                        await websocket.send_json({"type": "error", "code": "turn_not_open", "message": "No active turn"})
                        continue
                    turn_open = False
                    history = await _run_turn_until_done_or_disconnected(
                        websocket, db, robot_id, conversation.id, bytes(audio), history,
                    )
                    audio.clear()
                elif message_type == "session.end":
                    break
                else:
                    await websocket.send_json({"type": "error", "code": "unknown_voice_event", "message": f"Unknown event: {message_type}"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "code": "voice_gateway_error", "message": str(exc)[:300]})
        except Exception:
            pass
    finally:
        if robot is not None:
            try:
                await websocket.app.state.agent_client.request(robot, "POST", "/api/control/stop")
            except Exception:
                pass
        if conversation is not None:
            try:
                async with websocket.app.state.db.sessions() as db:
                    item = await db.get(type(conversation), conversation.id)
                    if item is not None:
                        await ConversationRepository.close_session(db, item)
            except Exception:
                pass
        registry.release(robot_id)


async def _run_turn_until_done_or_disconnected(
    websocket: WebSocket,
    db,
    robot_id: str,
    conversation_id,
    audio: bytes,
    history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep watching the car socket so provider work is cancelled on disconnect."""
    turn_task = asyncio.create_task(
        _process_turn(websocket, db, robot_id, conversation_id, audio, history),
        name=f"voice-turn-{robot_id}",
    )
    try:
        while True:
            receive_task = asyncio.create_task(websocket.receive())
            done, _ = await asyncio.wait({turn_task, receive_task}, return_when=asyncio.FIRST_COMPLETED)
            if turn_task in done:
                receive_task.cancel()
                await asyncio.gather(receive_task, return_exceptions=True)
                return turn_task.result()
            packet = receive_task.result()
            if packet.get("type") == "websocket.disconnect":
                turn_task.cancel()
                await asyncio.gather(turn_task, return_exceptions=True)
                raise WebSocketDisconnect(packet.get("code", 1000))
            await websocket.send_json({
                "type": "error",
                "code": "turn_in_progress",
                "message": "Wait for the current voice turn to finish",
            })
    finally:
        if not turn_task.done():
            turn_task.cancel()
            await asyncio.gather(turn_task, return_exceptions=True)


async def _process_turn(websocket: WebSocket, db, robot_id: str, conversation_id, audio: bytes, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    started = time.monotonic()
    turn = None

    async def emit(event: dict[str, Any]) -> None:
        await websocket.send_json(event)

    try:
        turn = await ConversationRepository.create_turn(db, conversation_id, robot_id, "")
        if not audio:
            raise VoiceProviderError("No speech audio was received")
        await emit({"type": "state", "state": "recognizing"})
        transcript = await websocket.app.state.voice_asr.transcribe(audio)
        if not transcript:
            raise VoiceProviderError("No speech was recognized")
        await ConversationRepository.set_transcript(db, turn, transcript)
        await emit({"type": "transcript.final", "text": transcript})
        await emit({"type": "state", "state": "thinking"})
        reply, tool_calls, next_history = await websocket.app.state.conversation_orchestrator.run_turn(
            db, robot_id, transcript, history, emit,
        )
        await ConversationRepository.set_turn_content(db, turn, reply, tool_calls)
        await emit({"type": "reply.text", "text": reply})
        await emit({"type": "reply.audio.start", "format": "pcm_s16le", "sample_rate": 24000, "channels": 1})
        async for chunk in websocket.app.state.voice_tts.synthesize(reply):
            await websocket.send_bytes(chunk)
        await emit({"type": "reply.audio.end"})
        latency_ms = round((time.monotonic() - started) * 1000)
        await ConversationRepository.finish_turn(db, turn, reply, tool_calls, latency_ms)
        await emit({"type": "state", "state": "followup", "idle_timeout_s": 20})
        return next_history
    except asyncio.CancelledError:
        latency_ms = round((time.monotonic() - started) * 1000)
        if turn is not None:
            await ConversationRepository.fail_turn(db, turn, "client_disconnected", latency_ms)
        raise
    except Exception as exc:
        latency_ms = round((time.monotonic() - started) * 1000)
        if turn is not None:
            await ConversationRepository.fail_turn(db, turn, str(exc), latency_ms)
        await emit({"type": "error", "code": "voice_turn_failed", "message": str(exc)[:300]})
        await emit({"type": "state", "state": "wake_listening"})
        return history
