from pathlib import Path

import pytest
from starlette.requests import Request

from mission_api.errors import ApiError
from mission_api.models import Robot
from mission_api.security import verify_http_token
from mission_api.seed import seed_robots


class FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_cars_yaml_only_inserts_missing_robot(tmp_path: Path, monkeypatch):
    path = tmp_path / "cars.yaml"
    path.write_text(
        "robots:\n"
        "  - id: existing\n    name: YAML Existing\n    base_url: http://existing:8001\n"
        "  - id: new_car\n    name: New Car\n    base_url: http://new-car:8001\n",
        encoding="utf-8",
    )
    existing = Robot(id="existing", name="DB Name", base_url="http://db:8001", capabilities={})

    async def fake_get(session, robot_id):
        del session
        return existing if robot_id == "existing" else None

    monkeypatch.setattr("mission_api.seed.RobotRepository.get", fake_get)
    session = FakeSession()

    inserted = await seed_robots(session, path)

    assert inserted == 1
    assert [robot.id for robot in session.added] == ["new_car"]
    assert existing.name == "DB Name"
    assert session.commits == 1


def _request(token: str = "", *, method: str = "GET", query_string: bytes = b"") -> Request:
    headers = []
    if token:
        headers.append((b"x-peacekeeper-token", token.encode("ascii")))
    return Request({"type": "http", "method": method, "path": "/api/robots", "query_string": query_string, "headers": headers})


def test_shared_token_accepts_exact_value_and_rejects_missing():
    verify_http_token(_request("secret-token"), "secret-token")

    with pytest.raises(ApiError) as caught:
        verify_http_token(_request(), "secret-token")

    assert caught.value.status_code == 401


def test_shared_token_allows_query_token_for_get_only():
    verify_http_token(_request(query_string=b"token=secret-token"), "secret-token")

    with pytest.raises(ApiError) as caught:
        verify_http_token(_request(method="POST", query_string=b"token=secret-token"), "secret-token")

    assert caught.value.status_code == 401
