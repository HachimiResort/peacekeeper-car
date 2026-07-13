import httpx
import pytest

from mission_api.agent_client import FleetAgentClient
from mission_api.errors import AgentError
from mission_api.models import Robot


@pytest.mark.asyncio
async def test_agent_client_attaches_token_and_normalizes_success():
    async def handler(request: httpx.Request):
        assert request.headers["X-Peacekeeper-Token"] == "test-token"
        return httpx.Response(200, json={"ok": True, "mode": "IDLE"})

    client = FleetAgentClient("test-token", 2, 5, 120)
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        headers={"X-Peacekeeper-Token": "test-token"},
        transport=httpx.MockTransport(handler),
    )
    robot = Robot(id="car_1", name="Car 1", base_url="http://car-1", capabilities={})

    result = await client.status(robot)

    assert result["mode"] == "IDLE"
    await client.close()


@pytest.mark.asyncio
async def test_agent_client_installs_audio_as_a_multipart_asset():
    async def handler(request: httpx.Request):
        assert request.url.path == "/api/audio/install"
        assert request.headers["X-Peacekeeper-Token"] == "test-token"
        assert b'filename="arrival.mp3"' in request.content
        assert b"audio-bytes" in request.content
        return httpx.Response(200, json={"ok": True, "asset": {"name": "arrival.mp3", "bytes": 11}})

    client = FleetAgentClient("test-token", 2, 5, 120)
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        headers={"X-Peacekeeper-Token": "test-token"},
        transport=httpx.MockTransport(handler),
    )
    robot = Robot(id="car_1", name="Car 1", base_url="http://car-1", capabilities={})

    result = await client.install_audio(robot, "arrival.mp3", b"audio-bytes")

    assert result["asset"]["name"] == "arrival.mp3"
    await client.close()


@pytest.mark.asyncio
async def test_agent_client_converts_timeout_to_domain_error():
    async def handler(request: httpx.Request):
        raise httpx.ReadTimeout("slow", request=request)

    client = FleetAgentClient("test-token", 2, 5, 120)
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    robot = Robot(id="car_1", name="Car 1", base_url="http://car-1", capabilities={})

    with pytest.raises(AgentError) as caught:
        await client.status(robot)

    assert caught.value.status_code == 504
    assert caught.value.code == "agent_timeout"
    await client.close()


@pytest.mark.asyncio
async def test_agent_client_reads_live_map_preview_as_bytes():
    async def handler(request: httpx.Request):
        assert request.url.path == "/api/mapping/live.png"
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\npreview", headers={"content-type": "image/png"})

    client = FleetAgentClient("test-token", 2, 5, 120)
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        headers={"X-Peacekeeper-Token": "test-token"},
        transport=httpx.MockTransport(handler),
    )
    robot = Robot(id="car_1", name="Car 1", base_url="http://car-1", capabilities={})

    image = await client.live_map_preview(robot)

    assert image.startswith(b"\x89PNG")
    await client.close()


@pytest.mark.asyncio
async def test_agent_client_reads_saved_map_preview_with_name():
    async def handler(request: httpx.Request):
        assert request.url.path == "/api/mapping/preview.png"
        assert request.url.params["name"] == "lab_first_map"
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nsaved", headers={"content-type": "image/png"})

    client = FleetAgentClient("test-token", 2, 5, 120)
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        headers={"X-Peacekeeper-Token": "test-token"},
        transport=httpx.MockTransport(handler),
    )
    robot = Robot(id="car_1", name="Car 1", base_url="http://car-1", capabilities={})

    image = await client.saved_map_preview(robot, "lab_first_map")

    assert image.startswith(b"\x89PNG")
    await client.close()


@pytest.mark.asyncio
async def test_agent_client_reads_video_sample_as_bytes():
    async def handler(request: httpx.Request):
        assert request.url.path == "/api/video/sample.jpg"
        return httpx.Response(200, content=b"\xff\xd8\xffsample", headers={"content-type": "image/jpeg"})

    client = FleetAgentClient("test-token", 2, 5, 120)
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        headers={"X-Peacekeeper-Token": "test-token"},
        transport=httpx.MockTransport(handler),
    )
    robot = Robot(id="car_1", name="Car 1", base_url="http://car-1", capabilities={})

    image = await client.video_sample(robot)

    assert image.startswith(b"\xff\xd8\xff")
    await client.close()


@pytest.mark.asyncio
async def test_agent_client_opens_video_stream():
    async def handler(request: httpx.Request):
        assert request.url.path == "/video.mjpg"
        return httpx.Response(
            200,
            content=b"--frame\r\nContent-Type: image/jpeg\r\n\r\n",
            headers={"content-type": "multipart/x-mixed-replace; boundary=frame"},
        )

    client = FleetAgentClient("test-token", 2, 5, 120)
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        headers={"X-Peacekeeper-Token": "test-token"},
        transport=httpx.MockTransport(handler),
    )
    robot = Robot(id="car_1", name="Car 1", base_url="http://car-1", capabilities={})

    response = await client.open_video_stream(robot)

    assert response.headers["content-type"].startswith("multipart/x-mixed-replace")
    await response.aclose()
    await client.close()


@pytest.mark.asyncio
async def test_agent_client_captures_and_reads_vision_results():
    async def handler(request: httpx.Request):
        if request.url.path == "/api/vision/capture":
            assert request.method == "POST"
            return httpx.Response(200, json={"ok": True, "model": "yolov8n.engine"})
        if request.url.path == "/api/vision/latest":
            return httpx.Response(200, json={"ok": True, "model": "yolov8n.engine"})
        if request.url.path == "/api/vision/latest.jpg":
            return httpx.Response(200, content=b"\xff\xd8vision", headers={"content-type": "image/jpeg"})
        raise AssertionError(request.url.path)

    client = FleetAgentClient("test-token", 2, 5, 120)
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        headers={"X-Peacekeeper-Token": "test-token"},
        transport=httpx.MockTransport(handler),
    )
    robot = Robot(id="car_1", name="Car 1", base_url="http://car-1", capabilities={})

    assert (await client.vision_capture(robot))["model"] == "yolov8n.engine"
    assert (await client.vision_latest(robot))["ok"] is True
    assert (await client.vision_latest_image(robot)).startswith(b"\xff\xd8")
    await client.close()


@pytest.mark.asyncio
async def test_agent_client_opens_vision_stream():
    async def handler(request: httpx.Request):
        assert request.url.path == "/api/vision/stream.mjpg"
        return httpx.Response(
            200,
            content=b"--frame\r\nContent-Type: image/jpeg\r\n\r\n",
            headers={"content-type": "multipart/x-mixed-replace; boundary=frame"},
        )

    client = FleetAgentClient("test-token", 2, 5, 120)
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        headers={"X-Peacekeeper-Token": "test-token"},
        transport=httpx.MockTransport(handler),
    )
    robot = Robot(id="car_1", name="Car 1", base_url="http://car-1", capabilities={})

    response = await client.open_vision_stream(robot)

    assert response.headers["content-type"].startswith("multipart/x-mixed-replace")
    await response.aclose()
    await client.close()
