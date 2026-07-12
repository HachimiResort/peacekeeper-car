import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from .agent_client import FleetAgentClient
from .models import Robot
from .repositories import RobotRepository


class WebSocketHub:
    def __init__(self, queue_size: int = 8):
        self.queue_size = queue_size
        self.queues: set[asyncio.Queue] = set()
        self.lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=self.queue_size)
        async with self.lock:
            self.queues.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self.lock:
            self.queues.discard(queue)

    async def publish(self, message: dict) -> None:
        async with self.lock:
            queues = list(self.queues)
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass


class StatusAggregator:
    def __init__(
        self,
        sessions: async_sessionmaker,
        agent: FleetAgentClient,
        hub: WebSocketHub,
        poll_s: float,
        offline_failures: int,
        last_seen_flush_s: float,
        max_concurrency: int,
    ):
        self.sessions = sessions
        self.agent = agent
        self.hub = hub
        self.poll_s = poll_s
        self.offline_failures = offline_failures
        self.last_seen_flush_s = last_seen_flush_s
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.cache: dict[str, dict[str, Any]] = {}
        self.failures: dict[str, int] = {}
        self.last_db_flush: dict[str, float] = {}
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="mission-status-aggregator")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            await self.task
            self.task = None

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return json.loads(json.dumps(self.cache, default=str))

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            async with self.sessions() as session:
                robots = await RobotRepository.list(session, enabled_only=True)
            active_ids = {robot.id for robot in robots}
            for robot_id in list(self.cache):
                if robot_id not in active_ids:
                    self.cache.pop(robot_id, None)
                    self.failures.pop(robot_id, None)
                    await self.hub.publish(
                        {"type": "robot_status", "robot_id": robot_id, "data": {"online": False}}
                    )
            await asyncio.gather(*(self._poll(robot) for robot in robots), return_exceptions=True)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.poll_s)
            except asyncio.TimeoutError:
                pass

    async def _poll(self, robot: Robot) -> None:
        async with self.semaphore:
            previous = self.cache.get(robot.id)
            try:
                status = await self.agent.status(robot)
                self.failures[robot.id] = 0
                now = datetime.now(timezone.utc)
                value = {"online": True, "last_seen": now.isoformat(), "status": status}
                self.cache[robot.id] = value
                await self._flush_last_seen(robot.id, now, force=not previous or not previous.get("online"))
            except Exception as exc:
                count = self.failures.get(robot.id, 0) + 1
                self.failures[robot.id] = count
                online = count < self.offline_failures and bool(previous and previous.get("online"))
                value = {
                    "online": online,
                    "last_seen": previous.get("last_seen") if previous else None,
                    "status": previous.get("status") if previous else None,
                    "error": str(exc),
                    "failure_count": count,
                }
                self.cache[robot.id] = value
            if previous != self.cache.get(robot.id):
                await self.hub.publish({"type": "robot_status", "robot_id": robot.id, "data": self.cache[robot.id]})

    async def _flush_last_seen(self, robot_id: str, now: datetime, force: bool) -> None:
        loop_time = asyncio.get_running_loop().time()
        if not force and loop_time - self.last_db_flush.get(robot_id, 0.0) < self.last_seen_flush_s:
            return
        async with self.sessions() as session:
            robot = await session.get(Robot, robot_id)
            if robot is not None:
                robot.last_seen = now
                await session.commit()
        self.last_db_flush[robot_id] = loop_time
