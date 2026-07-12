from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import ApiError
from .models import Robot


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.sessions() as session:
        yield session


async def get_robot_or_404(session: AsyncSession, robot_id: str) -> Robot:
    robot = await session.get(Robot, robot_id)
    if robot is None:
        raise ApiError(404, "robot_not_found", f"Robot '{robot_id}' was not found")
    return robot
