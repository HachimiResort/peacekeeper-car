import pytest

from mission_api.models import Robot
from mission_api.runtime import StatusAggregator, WebSocketHub


class FailingAgent:
    async def status(self, robot):
        raise RuntimeError(f"{robot.id} offline")


@pytest.mark.asyncio
async def test_status_cache_marks_offline_after_three_failures():
    hub = WebSocketHub()
    aggregator = StatusAggregator(
        sessions=None,
        agent=FailingAgent(),
        hub=hub,
        poll_s=1,
        offline_failures=3,
        last_seen_flush_s=30,
        max_concurrency=1,
    )
    robot = Robot(id="car_1", name="Car 1", base_url="http://car-1", capabilities={})
    aggregator.cache[robot.id] = {"online": True, "last_seen": "now", "status": {"mode": "IDLE"}}

    await aggregator._poll(robot)
    assert aggregator.cache[robot.id]["online"] is True
    await aggregator._poll(robot)
    assert aggregator.cache[robot.id]["online"] is True
    await aggregator._poll(robot)
    assert aggregator.cache[robot.id]["online"] is False


@pytest.mark.asyncio
async def test_websocket_hub_drops_oldest_message_for_slow_client():
    hub = WebSocketHub(queue_size=1)
    queue = await hub.subscribe()

    await hub.publish({"sequence": 1})
    await hub.publish({"sequence": 2})

    assert await queue.get() == {"sequence": 2}
