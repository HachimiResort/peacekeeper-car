from .models import Alert, Event, MapDeployment, Mission, Robot, StoredMap


def _iso(value):
    return value.isoformat() if value is not None else None


def robot_view(robot: Robot, runtime: dict | None = None) -> dict:
    runtime = runtime or {}
    return {
        "id": robot.id,
        "name": robot.name,
        "base_url": robot.base_url,
        "role": robot.role,
        "enabled": robot.enabled,
        "capabilities": robot.capabilities,
        "last_seen": _iso(robot.last_seen),
        "online": bool(runtime.get("online", False)),
        "runtime_status": runtime.get("status"),
        "runtime_error": runtime.get("error"),
    }


def mission_view(value: Mission) -> dict:
    return {
        "id": str(value.id),
        "mission_type": value.mission_type,
        "robot_id": value.robot_id,
        "state": value.state,
        "request": value.request_payload,
        "result": value.result_payload,
        "error": value.error,
        "created_at": _iso(value.created_at),
        "started_at": _iso(value.started_at),
        "finished_at": _iso(value.finished_at),
    }


def event_view(value: Event) -> dict:
    return {
        "id": str(value.id),
        "event_key": value.event_key,
        "robot_id": value.robot_id,
        "mission_id": str(value.mission_id) if value.mission_id else None,
        "event_type": value.event_type,
        "severity": value.severity,
        "payload": value.payload,
        "occurred_at": _iso(value.occurred_at),
        "received_at": _iso(value.received_at),
    }


def alert_view(value: Alert) -> dict:
    return {
        "id": str(value.id),
        "event_id": str(value.event_id),
        "state": value.state,
        "confirmed_by": value.confirmed_by,
        "confirmed_at": _iso(value.confirmed_at),
        "resolution": value.resolution,
        "action": value.action,
    }


def map_view(value: StoredMap) -> dict:
    return {
        "id": str(value.id),
        "logical_name": value.logical_name,
        "version": value.version,
        "yaml_sha256": value.yaml_sha256,
        "image_sha256": value.image_sha256,
        "bundle_sha256": value.bundle_sha256,
        "resolution": value.resolution,
        "origin": value.origin,
        "width": value.width,
        "height": value.height,
        "source_robot_id": value.source_robot_id,
        "created_at": _iso(value.created_at),
    }


def deployment_view(value: MapDeployment) -> dict:
    return {
        "id": str(value.id),
        "map_id": str(value.map_id),
        "robot_id": value.robot_id,
        "state": value.state,
        "installed_name": value.installed_name,
        "error": value.error,
        "created_at": _iso(value.created_at),
        "started_at": _iso(value.started_at),
        "finished_at": _iso(value.finished_at),
    }
