from typing import Any, Optional

import httpx

from .errors import AgentError
from .models import Robot


class FleetAgentClient:
    def __init__(self, shared_token: str, status_timeout_s: float, control_timeout_s: float, map_timeout_s: float):
        self.shared_token = shared_token
        self.status_timeout_s = status_timeout_s
        self.control_timeout_s = control_timeout_s
        self.map_timeout_s = map_timeout_s
        self.client = httpx.AsyncClient(headers={"X-Peacekeeper-Token": shared_token})

    async def close(self) -> None:
        await self.client.aclose()

    async def status(self, robot: Robot) -> dict[str, Any]:
        return await self.request(robot, "GET", "/api/status", timeout_s=self.status_timeout_s)

    async def request(
        self,
        robot: Robot,
        method: str,
        path: str,
        payload: Optional[dict] = None,
        timeout_s: Optional[float] = None,
    ) -> dict[str, Any]:
        url = f"{robot.base_url.rstrip('/')}{path}"
        try:
            response = await self.client.request(
                method,
                url,
                json=payload,
                timeout=timeout_s or self.control_timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise AgentError(504, "agent_timeout", f"Robot {robot.id} timed out", {"url": url}) from exc
        except httpx.HTTPError as exc:
            raise AgentError(503, "agent_unreachable", f"Robot {robot.id} is unreachable", {"url": url}) from exc
        data = self._json(response)
        if response.is_error:
            message = data.get("message") or data.get("error") or response.reason_phrase
            status = 409 if response.status_code == 409 else 502
            raise AgentError(status, "agent_rejected", str(message), {"agent_status": response.status_code})
        if isinstance(data, dict) and data.get("ok") is False:
            raise AgentError(409, "agent_rejected", str(data.get("message") or "Agent rejected request"), data)
        return data

    async def export_map(self, robot: Robot, map_name: str) -> bytes:
        url = f"{robot.base_url.rstrip('/')}/api/maps/export"
        try:
            response = await self.client.get(url, params={"name": map_name}, timeout=self.map_timeout_s)
        except httpx.TimeoutException as exc:
            raise AgentError(504, "agent_timeout", f"Map export from {robot.id} timed out") from exc
        except httpx.HTTPError as exc:
            raise AgentError(503, "agent_unreachable", f"Robot {robot.id} is unreachable") from exc
        if response.is_error:
            data = self._json(response)
            raise AgentError(502, "map_export_failed", str(data.get("message") or response.reason_phrase))
        return response.content

    async def live_map_status(self, robot: Robot) -> dict[str, Any]:
        return await self.request(robot, "GET", "/api/mapping/live-meta", timeout_s=self.status_timeout_s)

    async def live_map_preview(self, robot: Robot) -> bytes:
        return await self._binary_get(
            robot,
            "/api/mapping/live.png",
            self.control_timeout_s,
            "live_map_preview_failed",
        )

    async def video_sample(self, robot: Robot) -> bytes:
        return await self._binary_get(
            robot,
            "/api/video/sample.jpg",
            self.control_timeout_s,
            "video_sample_failed",
        )

    async def open_video_stream(self, robot: Robot) -> httpx.Response:
        url = f"{robot.base_url.rstrip('/')}/video.mjpg"
        return await self._open_stream(robot, url, "video_stream_failed")

    async def open_vision_stream(self, robot: Robot) -> httpx.Response:
        url = f"{robot.base_url.rstrip('/')}/api/vision/stream.mjpg"
        return await self._open_stream(robot, url, "vision_stream_failed")

    async def _open_stream(self, robot: Robot, url: str, error_code: str) -> httpx.Response:
        request = self.client.build_request("GET", url)
        try:
            response = await self.client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise AgentError(504, "agent_timeout", f"Request to {robot.id} timed out", {"url": url}) from exc
        except httpx.HTTPError as exc:
            raise AgentError(503, "agent_unreachable", f"Robot {robot.id} is unreachable", {"url": url}) from exc
        if response.is_error:
            data = self._json_bytes(await response.aread())
            await response.aclose()
            message = data.get("message") or response.reason_phrase
            status = 409 if response.status_code == 409 else 502
            raise AgentError(status, error_code, str(message), {"agent_status": response.status_code})
        return response

    async def saved_maps(self, robot: Robot) -> dict[str, Any]:
        return await self.request(robot, "GET", "/api/maps/saved", timeout_s=self.status_timeout_s)

    async def saved_map_preview(self, robot: Robot, map_name: str) -> bytes:
        return await self._binary_get(
            robot,
            "/api/mapping/preview.png",
            self.map_timeout_s,
            "saved_map_preview_failed",
            params={"name": map_name},
        )

    async def vision_status(self, robot: Robot) -> dict[str, Any]:
        return await self.request(robot, "GET", "/api/vision/status", timeout_s=self.status_timeout_s)

    async def vision_capture(self, robot: Robot) -> dict[str, Any]:
        return await self.request(robot, "POST", "/api/vision/capture", timeout_s=self.control_timeout_s)

    async def vision_latest(self, robot: Robot) -> dict[str, Any]:
        return await self.request(robot, "GET", "/api/vision/latest", timeout_s=self.status_timeout_s)

    async def vision_latest_image(self, robot: Robot) -> bytes:
        return await self._binary_get(
            robot,
            "/api/vision/latest.jpg",
            self.control_timeout_s,
            "vision_image_failed",
        )

    async def install_map(self, robot: Robot, bundle: bytes) -> dict[str, Any]:
        url = f"{robot.base_url.rstrip('/')}/api/maps/install"
        try:
            response = await self.client.post(
                url,
                files={"bundle": ("map.zip", bundle, "application/zip")},
                timeout=self.map_timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise AgentError(504, "agent_timeout", f"Map install on {robot.id} timed out") from exc
        except httpx.HTTPError as exc:
            raise AgentError(503, "agent_unreachable", f"Robot {robot.id} is unreachable") from exc
        data = self._json(response)
        if response.is_error or data.get("ok") is False:
            raise AgentError(409 if response.status_code == 409 else 502, "map_install_failed", str(data.get("message") or response.reason_phrase), data)
        return data

    async def _binary_get(
        self,
        robot: Robot,
        path: str,
        timeout_s: float,
        error_code: str,
        params: Optional[dict[str, Any]] = None,
    ) -> bytes:
        url = f"{robot.base_url.rstrip('/')}{path}"
        try:
            response = await self.client.get(url, params=params, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise AgentError(504, "agent_timeout", f"Request to {robot.id} timed out", {"url": url}) from exc
        except httpx.HTTPError as exc:
            raise AgentError(503, "agent_unreachable", f"Robot {robot.id} is unreachable", {"url": url}) from exc
        if response.is_error:
            data = self._json(response)
            message = data.get("message") or response.reason_phrase
            status = 409 if response.status_code == 409 else 502
            raise AgentError(status, error_code, str(message), {"agent_status": response.status_code})
        return response.content

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
            return value if isinstance(value, dict) else {"data": value}
        except Exception:
            return {"message": response.text[:1000]}

    @staticmethod
    def _json_bytes(raw: bytes) -> dict[str, Any]:
        try:
            value = httpx.Response(200, content=raw).json()
            return value if isinstance(value, dict) else {"data": value}
        except Exception:
            return {"message": raw[:1000].decode("utf-8", errors="replace")}
