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
