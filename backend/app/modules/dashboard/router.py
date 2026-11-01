"""Fleet dashboard summary and analytics (comparison, maintenance cost forecast)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DB, Viewer
from app.core.errors import UnprocessableData
from app.db.models import Alert, Equipment, FailureEvent, MaintenanceEvent, ModelVersion
from app.modules.equipment.service import get_equipment, latest_predictions

router = APIRouter(tags=["dashboard"])


class Summary(BaseModel):
    equipment_total: int
    by_status: dict[str, int]
    by_risk: dict[str, int]
    unscored: int
    open_alerts: int
    failures_last_30d: int
    average_health: float | None
    active_model: dict[str, Any] | None


@router.get("/dashboard/summary", response_model=Summary)
def summary(db: DB, _: Viewer) -> Summary:
    equipment = list(db.scalars(select(Equipment)))
    preds = latest_predictions(db, [e.id for e in equipment])
    by_status: dict[str, int] = {}
    for e in equipment:
        by_status[e.status] = by_status.get(e.status, 0) + 1
    by_risk = {lvl: 0 for lvl in ("low", "medium", "high", "critical")}
    for p in preds.values():
        by_risk[p.risk_level] += 1
    mv = db.scalar(select(ModelVersion).where(ModelVersion.is_active))
    return Summary(
        equipment_total=len(equipment),
        by_status=by_status,
        by_risk=by_risk,
        unscored=len(equipment) - len(preds),
        open_alerts=db.scalar(select(func.count()).select_from(Alert).where(Alert.status == "open")) or 0,
        failures_last_30d=db.scalar(
            select(func.count())
            .select_from(FailureEvent)
            .where(FailureEvent.occurred_at >= datetime.now(UTC) - timedelta(days=30))
        )
        or 0,
        average_health=round(sum(p.health_score for p in preds.values()) / len(preds), 1) if preds else None,
        active_model={
            "id": mv.id,
            "version": mv.version,
            "algorithm": mv.algorithm,
            "trained_at": mv.trained_at.isoformat(),
        }
        if mv
        else None,
    )


# ---------------------------------------------------------------- comparison
class ComparisonRow(BaseModel):
    equipment_id: uuid.UUID
    name: str
    type: str
    age_years: float | None
    risk_level: str | None
    health_score: int | None
    failure_probability: float | None
    rul_days: float | None
    failures_last_year: int
    mtbf_days: float | None
    maintenance_cost_last_year: float
    days_since_service: float | None


@router.get("/analytics/compare", response_model=list[ComparisonRow])
def compare(db: DB, _: Viewer, ids: list[uuid.UUID] = Query(min_length=2, max_length=10)) -> list[ComparisonRow]:
    """Side-by-side comparison. MTBF = observed days / failures over the last year (None if no failures)."""
    now = datetime.now(UTC)
    year_ago = now - timedelta(days=365)
    preds = latest_predictions(db, ids)
    rows = []
    for eq_id in ids:
        eq = get_equipment(db, eq_id)
        fails = (
            db.scalar(
                select(func.count())
                .select_from(FailureEvent)
                .where(FailureEvent.equipment_id == eq_id, FailureEvent.occurred_at >= year_ago)
            )
            or 0
        )
        cost = (
            db.scalar(
                select(func.coalesce(func.sum(MaintenanceEvent.cost), 0)).where(
                    MaintenanceEvent.equipment_id == eq_id, MaintenanceEvent.performed_at >= year_ago
                )
            )
            or 0
        )
        last_service = db.scalar(
            select(func.max(MaintenanceEvent.performed_at)).where(MaintenanceEvent.equipment_id == eq_id)
        )
        p = preds.get(eq_id)
        rows.append(
            ComparisonRow(
                equipment_id=eq.id,
                name=eq.name,
                type=eq.equipment_type.code,
                age_years=round((now.date() - eq.install_date).days / 365, 1) if eq.install_date else None,
                risk_level=p.risk_level if p else None,
                health_score=p.health_score if p else None,
                failure_probability=p.failure_probability if p else None,
                rul_days=p.rul_days if p else None,
                failures_last_year=fails,
                mtbf_days=round(365 / fails, 1) if fails else None,
                maintenance_cost_last_year=float(cost),
                days_since_service=round((now - last_service).total_seconds() / 86400, 1) if last_service else None,
            )
        )
    return rows


# ---------------------------------------------------------------- cost forecast
class CostRow(BaseModel):
    equipment_id: uuid.UUID
    name: str
    failure_probability: float
    expected_failure_cost: float
    preventive_cost: float
    recommendation: str


class CostForecast(BaseModel):
    horizon_days: int
    method: str
    total_expected_failure_cost: float
    total_preventive_cost_if_acting_on_flagged: float
    rows: list[CostRow]


def _avg_cost(db: DB, type_id: int, kind: str, failure_repairs: bool) -> float | None:
    stmt = (
        select(func.avg(MaintenanceEvent.cost))
        .join(Equipment, Equipment.id == MaintenanceEvent.equipment_id)
        .where(
            Equipment.equipment_type_id == type_id,
            MaintenanceEvent.maintenance_type == kind,
            MaintenanceEvent.cost.is_not(None),
        )
    )
    if kind == "repair":
        cond = MaintenanceEvent.notes.ilike("%failure%")
        stmt = stmt.where(cond if failure_repairs else ~cond)
    v = db.scalar(stmt)
    return float(v) if v is not None else None


@router.get("/analytics/cost-forecast", response_model=CostForecast)
def cost_forecast(db: DB, _: Viewer) -> CostForecast:
    """Deterministic expected-value estimate (not an ML prediction):
    expected failure cost = P(failure within horizon) × average historical corrective
    repair cost for that equipment type; compared with the average cost of an early
    (planned) repair. Acting is suggested when the expected failure cost is higher."""
    equipment = list(db.scalars(select(Equipment).where(Equipment.status != "retired")))
    preds = latest_predictions(db, [e.id for e in equipment])
    if not preds:
        raise UnprocessableData("No predictions available yet")
    mv = db.scalar(select(ModelVersion).where(ModelVersion.is_active))
    cache: dict[int, tuple[float, float]] = {}
    rows = []
    for e in equipment:
        p = preds.get(e.id)
        if p is None:
            continue
        if e.equipment_type_id not in cache:
            fail_cost = _avg_cost(db, e.equipment_type_id, "repair", True) or 10_000.0
            plan_cost = _avg_cost(db, e.equipment_type_id, "repair", False) or 1_500.0
            cache[e.equipment_type_id] = (fail_cost, plan_cost)
        fail_cost, plan_cost = cache[e.equipment_type_id]
        expected = p.failure_probability * fail_cost
        rows.append(
            CostRow(
                equipment_id=e.id,
                name=e.name,
                failure_probability=p.failure_probability,
                expected_failure_cost=round(expected, 2),
                preventive_cost=round(plan_cost, 2),
                recommendation="act now" if expected > plan_cost else "monitor",
            )
        )
    rows.sort(key=lambda r: r.expected_failure_cost, reverse=True)
    return CostForecast(
        horizon_days=mv.horizon_days if mv else 7,
        method="expected value: probability × historical average corrective repair cost (per equipment type)",
        total_expected_failure_cost=round(sum(r.expected_failure_cost for r in rows), 2),
        total_preventive_cost_if_acting_on_flagged=round(
            sum(r.preventive_cost for r in rows if r.recommendation == "act now"), 2
        ),
        rows=rows,
    )
