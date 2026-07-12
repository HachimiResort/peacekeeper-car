import secrets

from fastapi import Request, WebSocket

from .errors import ApiError


def verify_http_token(request: Request, expected: str) -> None:
    supplied = request.headers.get("X-Peacekeeper-Token", "") or request.query_params.get("token", "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise ApiError(401, "unauthorized", "Missing or invalid Peacekeeper token")


async def verify_websocket_token(websocket: WebSocket, expected: str) -> bool:
    supplied = websocket.query_params.get("token", "")
    if supplied and secrets.compare_digest(supplied, expected):
        return True
    await websocket.close(code=4401, reason="unauthorized")
    return False
