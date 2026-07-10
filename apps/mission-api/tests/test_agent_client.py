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
