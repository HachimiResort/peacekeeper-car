import os

os.environ.setdefault("PEACEKEEPER_SHARED_TOKEN", "test-shared-token")

from mission_api.app import app


def test_management_routes_and_response_models_are_in_openapi():
    schema = app.openapi()

    assert "/api/overview" in schema["paths"]
    assert "/api/alerts" in schema["paths"]
    assert "/api/map-deployments" in schema["paths"]
    assert "/api/robots/{robot_id}/control/clear-estop" in schema["paths"]
    assert "/api/robots/{robot_id}/mapping/live-meta" in schema["paths"]
    assert "/api/robots/{robot_id}/mapping/live.png" in schema["paths"]
    assert "/api/robots/{robot_id}/maps/saved" in schema["paths"]
    assert "/api/robots/{robot_id}/maps/saved/preview.png" in schema["paths"]
    assert schema["paths"]["/api/missions"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("MissionListResponse")
    assert "ErrorResponse" in schema["components"]["schemas"]
