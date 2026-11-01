"""Role × endpoint authorization matrix.

Every protected endpoint is checked for: no token → 401, insufficient role → 403.
(Success paths are covered in the module tests.)
"""

import uuid

import pytest

Z = str(uuid.uuid4())
# (method, path, minimum role)
ENDPOINTS = [
    ("GET", "/api/v1/users", "admin"),
    ("POST", "/api/v1/users", "admin"),
    ("PATCH", f"/api/v1/users/{Z}", "admin"),
    ("POST", "/api/v1/equipment-types", "admin"),
    ("POST", "/api/v1/equipment-types/1/sensors", "admin"),
    ("GET", "/api/v1/equipment-types", "viewer"),
    ("GET", "/api/v1/equipment", "viewer"),
    ("POST", "/api/v1/equipment", "engineer"),
    ("PATCH", f"/api/v1/equipment/{Z}", "engineer"),
    ("DELETE", f"/api/v1/equipment/{Z}", "admin"),
    ("POST", f"/api/v1/equipment/{Z}/readings:batch", "engineer"),
    ("POST", "/api/v1/ingestion/csv", "engineer"),
    ("GET", "/api/v1/ingestion/batches", "engineer"),
    ("GET", f"/api/v1/equipment/{Z}/readings", "viewer"),
    ("POST", f"/api/v1/equipment/{Z}/predict", "engineer"),
    ("GET", f"/api/v1/equipment/{Z}/predictions", "viewer"),
    ("POST", f"/api/v1/equipment/{Z}/simulate", "viewer"),
    ("GET", "/api/v1/alerts", "viewer"),
    ("POST", f"/api/v1/alerts/{Z}/acknowledge", "engineer"),
    ("POST", f"/api/v1/alerts/{Z}/resolve", "engineer"),
    ("POST", f"/api/v1/equipment/{Z}/maintenance", "engineer"),
    ("POST", f"/api/v1/equipment/{Z}/failures", "engineer"),
    ("GET", "/api/v1/models", "viewer"),
    ("POST", "/api/v1/models/train", "admin"),
    ("POST", "/api/v1/models/1/activate", "admin"),
    ("GET", "/api/v1/models/jobs", "admin"),
    ("GET", "/api/v1/dashboard/summary", "viewer"),
    ("GET", "/api/v1/config/risk-thresholds", "viewer"),
    ("PUT", "/api/v1/config/risk-thresholds", "admin"),
    ("GET", "/api/v1/reports/fleet.csv", "viewer"),
]
LOWER = {"viewer": [], "engineer": ["viewer"], "admin": ["viewer", "engineer"]}


@pytest.mark.parametrize(("method", "path", "minimum"), ENDPOINTS)
def test_requires_authentication(client, method, path, minimum):
    assert client.request(method, path).status_code == 401


@pytest.mark.parametrize(("method", "path", "minimum"), [e for e in ENDPOINTS if e[2] != "viewer"])
def test_insufficient_role_forbidden(client, auth, method, path, minimum):
    for role in LOWER[minimum]:
        r = client.request(method, path, headers=auth(role), json={})
        assert r.status_code == 403, f"{role} on {method} {path}: {r.status_code}"
