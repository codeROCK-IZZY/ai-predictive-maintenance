from datetime import date, timedelta

from sqlalchemy import select

from app.db.models import AuditLog
from app.db.session import new_session
from tests.conftest import PASSWORD, make_equipment


# ---------------------------------------------------------------- users
def test_admin_creates_user_and_duplicates_rejected(client, auth):
    h = auth("admin")
    body = {"email": "New@Example.com", "password": "long-enough-pass-1", "role": "engineer"}
    r = client.post("/api/v1/users", json=body, headers=h)
    assert r.status_code == 201 and r.json()["email"] == "new@example.com"
    assert client.post("/api/v1/users", json=body, headers=h).status_code == 409
    with new_session() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "user.create")) is not None


def test_user_validation(client, auth):
    h = auth("admin")
    assert (
        client.post(
            "/api/v1/users", json={"email": "x@example.com", "password": "short", "role": "viewer"}, headers=h
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/users",
            json={"email": "x@example.com", "password": "long-enough-pass-1", "role": "superuser"},
            headers=h,
        ).status_code
        == 422
    )


def test_admin_cannot_demote_self(client, auth):
    h = auth("admin")
    me = client.get("/api/v1/auth/me", headers=h).json()
    r = client.patch(f"/api/v1/users/{me['id']}", json={"role": "viewer"}, headers=h)
    assert r.status_code == 422


def test_deactivated_user_loses_access(client, auth):
    h = auth("admin")
    r = client.post("/api/v1/users", json={"email": "v@example.com", "password": PASSWORD, "role": "viewer"}, headers=h)
    uid = r.json()["id"]
    vh = {
        "Authorization": "Bearer "
        + client.post("/api/v1/auth/login", json={"email": "v@example.com", "password": PASSWORD}).json()[
            "access_token"
        ]
    }
    assert client.get("/api/v1/auth/me", headers=vh).status_code == 200
    assert client.delete(f"/api/v1/users/{uid}", headers=h).status_code == 204
    assert client.get("/api/v1/auth/me", headers=vh).status_code == 401


# ---------------------------------------------------------------- equipment
def test_equipment_types_seeded(client, auth):
    types = client.get("/api/v1/equipment-types", headers=auth("viewer")).json()
    assert {t["code"] for t in types} == {"pump", "compressor", "motor"}
    pump = next(t for t in types if t["code"] == "pump")
    assert "vibration_rms" in {s["code"] for s in pump["sensors"]}


def test_equipment_crud(client, auth):
    h = auth("engineer")
    eq = make_equipment(
        client,
        h,
        "PUM-9",
        install_date="2020-01-01",
        location="Line 1",
        operating_conditions={"duty_cycle": "continuous"},
    )
    assert eq["equipment_type_code"] == "pump" and eq["latest_prediction"] is None
    r = client.patch(f"/api/v1/equipment/{eq['id']}", json={"status": "maintenance"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "maintenance"
    assert client.get(f"/api/v1/equipment/{eq['id']}", headers=auth("viewer")).json()["status"] == "maintenance"
    assert client.delete(f"/api/v1/equipment/{eq['id']}", headers=h).status_code == 403
    assert client.delete(f"/api/v1/equipment/{eq['id']}", headers=auth("admin")).status_code == 204
    assert client.get(f"/api/v1/equipment/{eq['id']}", headers=h).status_code == 404


def test_equipment_validation_and_conflicts(client, auth):
    h = auth("engineer")
    make_equipment(client, h, "DUP-1")
    dup = client.post(
        "/api/v1/equipment", json={"name": "x", "serial_number": "DUP-1", "equipment_type_code": "pump"}, headers=h
    )
    assert dup.status_code == 409
    unknown = client.post(
        "/api/v1/equipment", json={"name": "x", "serial_number": "U-1", "equipment_type_code": "turbine"}, headers=h
    )
    assert unknown.status_code == 404
    future = (date.today() + timedelta(days=5)).isoformat()
    bad = client.post(
        "/api/v1/equipment",
        json={"name": "x", "serial_number": "F-1", "equipment_type_code": "pump", "install_date": future},
        headers=h,
    )
    assert bad.status_code == 422
    assert bad.json()["errors"]


def test_equipment_filters_and_pagination(client, auth):
    h = auth("engineer")
    for i in range(3):
        make_equipment(client, h, f"PUM-{i}")
    make_equipment(client, h, "MOT-1", type_code="motor", location="Plant Z")
    page = client.get("/api/v1/equipment?type=pump&limit=2", headers=h).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert client.get("/api/v1/equipment?q=plant z", headers=h).json()["total"] == 1
    assert client.get("/api/v1/equipment?risk=bogus", headers=h).status_code == 422


def test_admin_adds_sensor_definition(client, auth):
    h = auth("admin")
    t = client.post("/api/v1/equipment-types", json={"code": "fan", "name": "Fan"}, headers=h)
    assert t.status_code == 201
    bad = client.post(
        f"/api/v1/equipment-types/{t.json()['id']}/sensors",
        json={"code": "vib", "name": "V", "unit": "mm/s", "min_valid": 5, "max_valid": 1},
        headers=h,
    )
    assert bad.status_code == 422
    ok = client.post(
        f"/api/v1/equipment-types/{t.json()['id']}/sensors",
        json={"code": "vib", "name": "V", "unit": "mm/s", "min_valid": 0, "max_valid": 50},
        headers=h,
    )
    assert ok.status_code == 201
