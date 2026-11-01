import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.api.deps import DB, Engineer, Viewer
from app.core.errors import Conflict, NotFound
from app.db.models import Alert, Equipment

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    equipment_id: uuid.UUID
    equipment_name: str | None = None
    prediction_id: uuid.UUID | None
    previous_risk: str | None
    new_risk: str
    status: str
    message: str
    acknowledged_by: uuid.UUID | None
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime


class AlertPage(BaseModel):
    items: list[AlertOut]
    total: int


def _out(a: Alert, name: str | None) -> AlertOut:
    out = AlertOut.model_validate(a)
    out.equipment_name = name
    return out


@router.get("", response_model=AlertPage)
def list_alerts(
    db: DB,
    _: Viewer,
    status: str | None = Query(default=None, pattern="^(open|acknowledged|resolved)$"),
    risk: str | None = Query(default=None, pattern="^(medium|high|critical)$"),
    equipment_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AlertPage:
    stmt = select(Alert, Equipment.name).join(Equipment, Equipment.id == Alert.equipment_id)
    if status:
        stmt = stmt.where(Alert.status == status)
    if risk:
        stmt = stmt.where(Alert.new_risk == risk)
    if equipment_id:
        stmt = stmt.where(Alert.equipment_id == equipment_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(Alert.created_at.desc()).limit(limit).offset(offset)).all()
    return AlertPage(items=[_out(a, n) for a, n in rows], total=total)


def _get(db: DB, alert_id: uuid.UUID) -> Alert:
    a = db.get(Alert, alert_id)
    if a is None:
        raise NotFound("Alert not found")
    return a


@router.post("/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge(alert_id: uuid.UUID, db: DB, user: Engineer) -> AlertOut:
    a = _get(db, alert_id)
    if a.status != "open":
        raise Conflict(f"Alert is already {a.status}")
    a.status, a.acknowledged_by, a.acknowledged_at = "acknowledged", user.id, datetime.now(UTC)
    db.commit()
    return _out(a, None)


@router.post("/{alert_id}/resolve", response_model=AlertOut)
def resolve(alert_id: uuid.UUID, db: DB, user: Engineer) -> AlertOut:
    a = _get(db, alert_id)
    if a.status == "resolved":
        raise Conflict("Alert is already resolved")
    now = datetime.now(UTC)
    if a.acknowledged_by is None:
        a.acknowledged_by, a.acknowledged_at = user.id, now
    a.status, a.resolved_at = "resolved", now
    db.commit()
    return _out(a, None)
