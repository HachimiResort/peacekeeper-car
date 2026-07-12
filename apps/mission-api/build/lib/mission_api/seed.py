from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Robot
from .repositories import RobotRepository


async def seed_robots(session: AsyncSession, path: Path) -> int:
    if not path.exists():
        return 0
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    robots = document.get("robots", [])
    if not isinstance(robots, list):
        raise ValueError("cars.yaml must contain a robots list")
    inserted = 0
    for item in robots:
        robot_id = str(item["id"])
        if await RobotRepository.get(session, robot_id) is not None:
            continue
        session.add(
            Robot(
                id=robot_id,
                name=str(item.get("name") or robot_id),
                base_url=str(item["base_url"]).rstrip("/"),
                role=str(item.get("role") or "robot"),
                enabled=bool(item.get("enabled", True)),
                capabilities=dict(item.get("capabilities") or {}),
            )
        )
        inserted += 1
    await session.commit()
    return inserted
