"""Integration: load a small synthetic fleet, train through the real pipeline,
then exercise prediction, alerts, simulation, analytics, reports and the
model registry over the HTTP API.

The fleet is small (fast) so metrics here are NOT meaningful; see
ml/reports/evaluation.md for the full evaluation.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db.models import Equipment, ModelVersion, Prediction
from app.db.session import new_session
from app.jobs import queue
from app.jobs.worker import drain
from app.main import app
from app.ml import registry, scoring
from app.ml.demo import load_demo
from app.ml.features import FEATURE_NAMES
from app.ml.pipeline import train_from_db
from app.ml.report import render_markdown
from app.ml.synthetic import FleetConfig
from tests.conftest import create_user, login, reset_database

pytestmark = pytest.mark.slow

# Fixed end date: the fleet (and therefore the trained model) is identical on every run.
FIXED_END = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def trained() -> Iterator[dict]:
    reset_database()
    with new_session() as db:
        stats = load_demo(db, FleetConfig(n_units=15, days=150, freq_minutes=60, seed=5, end=FIXED_END))
        db.commit()
        mv = train_from_db(db, algorithm="auto", compare=False, activate=True)
        db.commit()
        info = {"stats": stats, "model_id": mv.id, "algorithm": mv.algorithm}
    create_user("admin@example.com", "admin")
    create_user("eng@example.com", "engineer")
    create_user("view@example.com", "viewer")
    yield info


@pytest.fixture
def api(trained) -> Iterator[TestClient]:
    from app.modules.auth.router import login_limiter

    login_limiter.reset()
    with TestClient(app) as c:
        yield c


def _ids(type_code: str | None = None) -> list:
    with new_session() as db:
        stmt = select(Equipment)
        if type_code:
            from app.db.models import EquipmentType

            stmt = stmt.join(EquipmentType).where(EquipmentType.code == type_code)
        return [e.id for e in db.scalars(stmt.order_by(Equipment.serial_number))]


def test_training_registered_active_model(trained):
    assert trained["stats"]["equipment"] == 15 and trained["stats"]["failures"] > 0
    with new_session() as db:
        mv = db.get(ModelVersion, trained["model_id"])
        assert mv.is_active and mv.version == 1 and mv.algorithm == "xgboost"
        m = mv.metrics
        assert "cross_validation" in m and "holdout" in m and "rul" in m
        assert 0 <= m["holdout"]["production_calibrated"]["brier"] <= 1
        assert mv.feature_names == list(FEATURE_NAMES)
        md = render_markdown(m)
        assert "# Model evaluation report" in md and "| xgboost |" in md and "Limitations" in md


def test_predict_endpoint_returns_explained_prediction(api):
    h = login(api, "eng@example.com")
    eq_id = _ids("pump")[0]
    r = api.post(f"/api/v1/equipment/{eq_id}/predict", headers=h)
    assert r.status_code == 201, r.text
    p = r.json()
    assert 0 <= p["failure_probability"] <= 1
    assert p["health_score"] == round(100 * (1 - p["failure_probability"]))
    assert p["risk_level"] in ("low", "medium", "high", "critical")
    assert p["explanation"]["method"] == "shap" and p["explanation"]["top_factors"]
    assert all(r["source"] == "rule" for r in p["recommendations"])
    assert p["model"]["version"] == 1
    hist = api.get(f"/api/v1/equipment/{eq_id}/predictions", headers=login(api, "view@example.com")).json()
    assert len(hist) >= 1
    listed = api.get("/api/v1/equipment?type=pump", headers=h).json()["items"]
    assert any(e["latest_prediction"] for e in listed)


def test_alert_raised_only_when_risk_increases(api, trained):
    eq_id = _ids()[0]
    with new_session() as db:
        eq = db.get(Equipment, eq_id)
        loaded = registry.get_active(db)
        p1 = scoring.score_equipment(db, eq, loaded)
        p1.risk_level = "low"
        db.flush()
        p2 = Prediction(
            equipment_id=eq.id,
            model_version_id=loaded.version.id,
            as_of=p1.as_of + timedelta(hours=1),
            failure_probability=0.7,
            health_score=30,
            risk_level="critical",
            explanation={},
            recommendations=[],
        )
        db.add(p2)
        db.flush()
        a = scoring.maybe_alert(db, eq, p2, "low", 7)
        assert a is not None and a.new_risk == "critical" and "risk increased from low to critical" in a.message
        assert scoring.maybe_alert(db, eq, p2, "critical", 7) is None  # unchanged → no alert
        assert scoring.maybe_alert(db, eq, p2, None, 7) is not None  # first prediction already high → alert
        db.commit()
        alert_id = a.id
    h = login(api, "eng@example.com")
    assert api.post(f"/api/v1/alerts/{alert_id}/acknowledge", headers=h).json()["status"] == "acknowledged"
    assert api.post(f"/api/v1/alerts/{alert_id}/acknowledge", headers=h).status_code == 409
    assert api.post(f"/api/v1/alerts/{alert_id}/resolve", headers=h).json()["status"] == "resolved"
    page = api.get("/api/v1/alerts?status=resolved", headers=login(api, "view@example.com")).json()
    assert any(x["id"] == str(alert_id) for x in page["items"])


def test_simulation_raises_risk_and_stores_nothing(api):
    h = login(api, "view@example.com")
    eq_id = _ids("pump")[1]
    with new_session() as db:
        before = db.scalar(text("SELECT count(*) FROM predictions"))
    r = api.post(
        f"/api/v1/equipment/{eq_id}/simulate",
        headers=h,
        json={"failure_mode": "bearing_wear", "severity": 1.2, "ramp_days": 10},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    # Injected bearing wear must raise the risk substantially and be attributed to bearing signals.
    assert d["simulated"]["failure_probability"] > d["baseline"]["failure_probability"] + 0.1
    assert d["simulated"]["top_factors"][0]["signal"] in ("vibration_rms", "bearing_temp_rise")
    with new_session() as db:
        assert db.scalar(text("SELECT count(*) FROM predictions")) == before
    bad = api.post(f"/api/v1/equipment/{eq_id}/simulate", headers=h, json={"failure_mode": "winding_fault"})
    assert bad.status_code == 422  # pumps have no windings in this catalog


def test_score_all_job_and_dashboard(api):
    with new_session() as db:
        queue.enqueue(db, "score_all", {})
        db.commit()
    drain()
    h = login(api, "view@example.com")
    s = api.get("/api/v1/dashboard/summary", headers=h).json()
    assert s["equipment_total"] == 15 and s["unscored"] == 0
    assert sum(s["by_risk"].values()) == 15 and s["active_model"]["version"] == 1


def test_analytics_compare_and_cost_forecast(api):
    h = login(api, "view@example.com")
    ids = _ids()[:3]
    rows = api.get("/api/v1/analytics/compare?" + "&".join(f"ids={i}" for i in ids), headers=h).json()
    assert len(rows) == 3 and all("mtbf_days" in r for r in rows)
    assert api.get(f"/api/v1/analytics/compare?ids={ids[0]}", headers=h).status_code == 422
    cf = api.get("/api/v1/analytics/cost-forecast", headers=h).json()
    assert cf["rows"] and cf["rows"][0]["expected_failure_cost"] >= cf["rows"][-1]["expected_failure_cost"]
    for row in cf["rows"]:
        assert row["recommendation"] == (
            "act now" if row["expected_failure_cost"] > row["preventive_cost"] else "monitor"
        )


def test_reports_csv_and_formula_injection_guard(api):
    h = login(api, "eng@example.com")
    eq_id = _ids()[0]
    api.post(
        f"/api/v1/equipment/{eq_id}/maintenance",
        headers=h,
        json={
            "performed_at": datetime.now(UTC).isoformat(),
            "maintenance_type": "inspection",
            "notes": '=HYPERLINK("http://evil")',
            "cost": 120.5,
        },
    )
    csv_text = api.get("/api/v1/reports/maintenance.csv?days=2", headers=h).text
    assert "'=HYPERLINK" in csv_text and "\n=HYPERLINK" not in csv_text
    fleet = api.get("/api/v1/reports/fleet.csv", headers=h)
    assert fleet.headers["content-type"].startswith("text/csv") and fleet.text.count("\n") == 16


def test_maintenance_and_failure_logging(api):
    h = login(api, "eng@example.com")
    eq_id = _ids()[2]
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    assert (
        api.post(
            f"/api/v1/equipment/{eq_id}/maintenance",
            headers=h,
            json={"performed_at": future, "maintenance_type": "repair"},
        ).status_code
        == 422
    )
    assert (
        api.post(
            f"/api/v1/equipment/{eq_id}/maintenance",
            headers=h,
            json={"performed_at": datetime.now(UTC).isoformat(), "maintenance_type": "repair", "cost": -1},
        ).status_code
        == 422
    )
    r = api.post(
        f"/api/v1/equipment/{eq_id}/failures",
        headers=h,
        json={"occurred_at": datetime.now(UTC).isoformat(), "failure_mode": "bearing_wear"},
    )
    assert r.status_code == 201
    assert any(f["id"] == r.json()["id"] for f in api.get(f"/api/v1/equipment/{eq_id}/failures", headers=h).json())


def test_thresholds_change_risk_classification(api):
    h = login(api, "admin@example.com")
    assert (
        api.put(
            "/api/v1/config/risk-thresholds", headers=h, json={"medium": 0.5, "high": 0.3, "critical": 0.9}
        ).status_code
        == 422
    )  # not ordered
    ok = api.put(
        "/api/v1/config/risk-thresholds", headers=h, json={"medium": 0.0001, "high": 0.0002, "critical": 0.0003}
    )
    assert ok.status_code == 200
    eq_id = _ids("motor")[0]
    p = api.post(f"/api/v1/equipment/{eq_id}/predict", headers=h).json()
    th = {"low": 0.0, "medium": 0.0001, "high": 0.0002, "critical": 0.0003}
    assert p["risk_level"] == scoring.risk_level(p["failure_probability"], th)
    api.put("/api/v1/config/risk-thresholds", headers=h, json={"medium": 0.1, "high": 0.3, "critical": 0.6})


def test_tampered_artifact_is_refused(api, trained):
    with new_session() as db:
        mv = db.get(ModelVersion, trained["model_id"])
        path = mv.artifact_uri.removeprefix("file://")
    artifact = Path(path)
    original = artifact.read_bytes()
    try:
        artifact.write_bytes(original + b"tampered")
        registry._cache.clear()
        r = api.post(f"/api/v1/equipment/{_ids()[0]}/predict", headers=login(api, "eng@example.com"))
        assert r.status_code == 503 and "integrity" in r.json()["detail"]
    finally:
        artifact.write_bytes(original)
        registry._cache.clear()


def test_train_job_registers_new_version_and_activation(api):
    h = login(api, "admin@example.com")
    job = api.post(
        "/api/v1/models/train",
        headers=h,
        json={"algorithm": "logistic_regression", "compare": False, "activate": False},
    ).json()
    drain()
    done = api.get(f"/api/v1/models/jobs/{job['id']}", headers=h).json()
    assert done["status"] == "succeeded", done["last_error"]
    models = api.get("/api/v1/models", headers=h).json()
    assert [m["version"] for m in models][:2] == [2, 1]
    v2 = models[0]
    assert v2["algorithm"] == "logistic_regression" and not v2["is_active"]
    act = api.post(f"/api/v1/models/{v2['id']}/activate", headers=h).json()
    assert act["is_active"]
    with new_session() as db:
        active = db.scalars(select(ModelVersion).where(ModelVersion.is_active)).all()
        assert len(active) == 1 and active[0].version == 2


def test_ready_endpoint_reports_model(api):
    assert api.get("/api/health/ready").json() == {"status": "ok", "database": "ok", "active_model": True}


def test_backfill_creates_history_and_alerts_from_past_data_only(api):
    from app.ml.backfill import backfill

    with new_session() as db:
        stats = backfill(db, days=10, step_hours=12)
        db.commit()
        assert stats["predictions"] > 0
        eq_id = _ids()[3]
        rows = db.scalars(select(Prediction).where(Prediction.equipment_id == eq_id).order_by(Prediction.as_of)).all()
        times = [r.as_of for r in rows]
        assert times == sorted(times) and len(times) >= 15
        assert all(t.hour % 12 == 0 for t in times[:-1])
        # Re-running replaces rather than duplicates the backfilled window.
        before = db.scalar(text("SELECT count(*) FROM predictions"))
        backfill(db, days=10, step_hours=12)
        db.commit()
        assert db.scalar(text("SELECT count(*) FROM predictions")) == before
