import base64
import gzip
import json
import struct

import httpx
import pytest
from pydantic import ValidationError

from mission_api.errors import ApiError
from mission_api.models import Robot
from mission_api.voice.orchestrator import ConversationOrchestrator
from mission_api.voice.providers import ArkResponsesClient, VolcASRClient, VolcTTSClient, _provider_http_error
from mission_api.voice.tools import DriveArgs, RobotToolExecutor, TurnAngleArgs


class RecordingAgent:
    def __init__(self):
        self.calls = []

    async def request(self, robot, method, path, payload=None):
        self.calls.append((robot.id, method, path, payload))
        return {"ok": True}


@pytest.mark.asyncio
async def test_short_drive_has_fixed_speed_ttl_and_always_stops(monkeypatch):
    agent = RecordingAgent()
    executor = RobotToolExecutor(agent)
    robot = Robot(id="car_1", name="Car 1", base_url="http://car-1", capabilities={})

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("mission_api.voice.tools.asyncio.sleep", no_sleep)
    result = await executor._drive(robot, DriveArgs(direction="left", duration_ms=1000))

    assert result["ok"] is True
    assert agent.calls[0][2] == "/api/control/manual_cmd"
    assert agent.calls[0][3] == {
        "linear_x": 0.0,
        "linear_y": 0.0,
        "angular_z": 0.9,
        "ttl_ms": 1000,
        "source": "doubao-voice",
    }
    assert agent.calls[-1][2] == "/api/control/stop"


def test_voice_tool_arguments_enforce_allowlist_and_motion_limits():
    with pytest.raises(ValidationError):
        DriveArgs(direction="forward", duration_ms=1001)
    with pytest.raises(ValidationError):
        DriveArgs(direction="navigate", duration_ms=100)
    with pytest.raises(ValidationError):
        TurnAngleArgs(direction="left", angle_degrees=181)


@pytest.mark.asyncio
async def test_turn_by_angle_calculates_longer_ttl_and_always_stops(monkeypatch):
    agent = RecordingAgent()
    executor = RobotToolExecutor(agent)
    robot = Robot(id="car_1", name="Car 1", base_url="http://car-1", capabilities={})

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("mission_api.voice.tools.asyncio.sleep", no_sleep)
    result = await executor._turn_by_angle(robot, TurnAngleArgs(direction="right", angle_degrees=90))

    assert result["ok"] is True
    assert result["duration_ms"] == 2007
    assert agent.calls[0][3] == {
        "linear_x": 0.0,
        "linear_y": 0.0,
        "angular_z": -0.9,
        "ttl_ms": 2007,
        "source": "doubao-voice-angle",
    }
    assert agent.calls[-1][2] == "/api/control/stop"


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_before_robot_lookup():
    with pytest.raises(ApiError) as caught:
        await RobotToolExecutor(RecordingAgent()).execute(None, "car_1", "navigate", {})
    assert caught.value.code == "unknown_voice_tool"


def test_provider_http_errors_keep_safe_vendor_detail():
    error = _provider_http_error("ASR handshake", 401, b'{"error":"request and grant appid mismatch"}')

    assert str(error) == 'ASR handshake failed with HTTP 401: {"error":"request and grant appid mismatch"}'


class ScriptedArk:
    def __init__(self, responses):
        self.responses = list(responses)
        self.inputs = []

    async def create(self, input_items, tools):
        self.inputs.append((list(input_items), tools))
        return self.responses.pop(0)


class FakeTools:
    async def execute(self, session, robot_id, name, arguments):
        assert robot_id == "car_1"
        return {"ok": True, "left": True, "right": True}


@pytest.mark.asyncio
async def test_function_call_round_trip_returns_text_and_audit():
    ark = ScriptedArk([
        {"output": [{"type": "function_call", "call_id": "call-1", "name": "set_lights", "arguments": '{"left":true,"right":true}'}]},
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "灯已经打开了。"}]}]},
    ])
    events = []

    async def emit(event):
        events.append(event)

    reply, audit, history = await ConversationOrchestrator(ark, FakeTools()).run_turn(
        None, "car_1", "打开灯", [], emit,
    )

    assert reply == "灯已经打开了。"
    assert audit[0]["name"] == "set_lights"
    assert [event["type"] for event in events] == ["tool.call", "tool.result"]
    assert history[-1]["role"] == "assistant"
    assert any(item.get("type") == "function_call_output" for item in ark.inputs[1][0])


@pytest.mark.asyncio
async def test_ark_responses_request_disables_storage_and_thinking():
    captured = {}

    async def handler(request: httpx.Request):
        captured.update(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(200, json={"output": []})

    client = ArkResponsesClient("secret", "doubao-test", "https://ark.example/api/v3")
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="https://ark.example/api/v3",
        headers={"Authorization": "Bearer secret"},
        transport=httpx.MockTransport(handler),
    )
    await client.create([{"role": "user", "content": []}], [])
    await client.close()

    assert captured["model"] == "doubao-test"
    assert captured["store"] is False
    assert captured["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_tts_decodes_chunked_pcm_lines():
    pcm = b"\x01\x02\x03\x04"

    async def handler(request: httpx.Request):
        assert json.loads(request.content)["req_params"]["audio_params"]["sample_rate"] == 24000
        assert request.headers["X-Api-Request-Id"]
        body = json.dumps({"code": 0, "data": base64.b64encode(pcm).decode()}) + "\n" + json.dumps({"code": 20000000}) + "\n"
        return httpx.Response(200, text=body)

    client = VolcTTSClient("app", "token", "resource", "voice", "https://tts.example")
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    chunks = [chunk async for chunk in client.synthesize("你好")]
    await client.close()

    assert chunks == [pcm]


def test_asr_binary_parser_reads_gzip_json_and_final_flag():
    value = {"result": {"text": "停车", "utterances": [{"definite": True}]}}
    body = gzip.compress(json.dumps(value).encode())
    raw = bytes((0x11, 0x93, 0x11, 0x00)) + struct.pack(">iI", -1, len(body)) + body

    parsed = VolcASRClient._parse(raw)

    assert parsed["is_last_package"] is True
    assert parsed["sequence"] == -1
    assert parsed["payload_msg"]["result"]["text"] == "停车"


def test_asr_optimized_protocol_uses_server_assigned_sequences():
    client = VolcASRClient("app", "token", "resource", "wss://asr.example")

    request = client._full_request(16000)
    request_size = struct.unpack(">I", request[4:8])[0]
    request_payload = json.loads(gzip.decompress(request[8 : 8 + request_size]))
    audio = client._audio_frame(b"\x01\x02")
    final = client._audio_frame(b"", last=True)

    assert request[:4] == bytes((0x11, 0x10, 0x11, 0x00))
    assert request_payload["audio"] == {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1}
    assert request_payload["request"]["enable_nonstream"] is True
    assert audio[:4] == bytes((0x11, 0x20, 0x01, 0x00))
    assert final[:4] == bytes((0x11, 0x22, 0x01, 0x00))
