"""Equipment, equipment types and sensor definitions."""

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.db.models import Equipment, EquipmentType, Prediction, SensorDefinition
from app.modules.equipment.catalog import EQUIPMENT_TYPES
from app.modules.equipment.schemas import (
    EquipmentCreate,
    EquipmentOut,
    EquipmentUpdate,
    LatestPrediction,
)


def seed_catalog(db: Session) -> None:
    """Idempotently insert the default equipment types and sensors."""
    for spec in EQUIPMENT_TYPES:
        et = db.scalar(select(EquipmentType).where(EquipmentType.code == spec.code))
        if et is None:
            et = EquipmentType(code=spec.code, name=spec.name, description=spec.description)
            db.add(et)
            db.flush()
        existing = {s.code for s in et.sensors}
        for s in spec.sensors:
            if s.code not in existing:
                db.add(
                    SensorDefinition(
                        equipment_type_id=et.id,
                        code=s.code,
                        name=s.name,
                        unit=s.unit,
                        min_valid=s.min_valid,
                        max_valid=s.max_valid,
                    )
                )
    db.flush()


def get_type_by_code(db: Session, code: str) -> EquipmentType:
    et = db.scalar(select(EquipmentType).where(EquipmentType.code == code))
    if et is None:
        raise NotFound(f"Unknown equipment type '{code}'")
    return et


def get_equipment(db: Session, equipment_id: uuid.UUID) -> Equipment:
    eq = db.get(Equipment, equipment_id)
    if eq is None:
        raise NotFound("Equipment not found")
    return eq


def latest_predictions(db: Session, equipment_ids: list[uuid.UUID]) -> dict[uuid.UUID, Prediction]:
    if not equipment_ids:
        return {}
    stmt = (
        select(Prediction)
        .where(Prediction.equipment_id.in_(equipment_ids))
        .distinct(Prediction.equipment_id)
        .order_by(Prediction.equipment_id, Prediction.as_of.desc(), Prediction.created_at.desc())
    )
    return {p.equipment_id: p for p in db.scalars(stmt)}


def to_out(eq: Equipment, pred: Prediction | None) -> EquipmentOut:
    return EquipmentOut(
        id=eq.id,
        name=eq.name,
        serial_number=eq.serial_number,
        manufacturer=eq.manufacturer,
        model=eq.model,
        install_date=eq.install_date,
        location=eq.location,
        operating_conditions=eq.operating_conditions,
        status=eq.status,
        equipment_type_code=eq.equipment_type.code,
        equipment_type_name=eq.equipment_type.name,
        created_at=eq.created_at,
        updated_at=eq.updated_at,
        latest_prediction=LatestPrediction(
            id=pred.id,
            as_of=pred.as_of,
            failure_probability=pred.failure_probability,
            health_score=pred.health_score,
            risk_level=pred.risk_level,
            rul_days=pred.rul_days,
        )
        if pred
        else None,
    )


def list_equipment(
    db: Session,
    *,
    type_code: str | None,
    status: str | None,
    risk: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> tuple[list[EquipmentOut], int]:
    stmt = select(Equipment).join(EquipmentType)
    if type_code:
        stmt = stmt.where(EquipmentType.code == type_code)
    if status:
        stmt = stmt.where(Equipment.status == status)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Equipment.name).like(like),
                func.lower(Equipment.serial_number).like(like),
                func.lower(Equipment.location).like(like),
            )
        )
    if risk:
        latest = (
            select(Prediction.equipment_id, Prediction.risk_level)
            .distinct(Prediction.equipment_id)
            .order_by(Prediction.equipment_id, Prediction.as_of.desc(), Prediction.created_at.desc())
            .subquery()
        )
        stmt = stmt.join(latest, latest.c.equipment_id == Equipment.id).where(latest.c.risk_level == risk)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(Equipment.name).limit(limit).offset(offset)))
    preds = latest_predictions(db, [e.id for e in rows])
    return [to_out(e, preds.get(e.id)) for e in rows], total


def create_equipment(db: Session, body: EquipmentCreate) -> Equipment:
    et = get_type_by_code(db, body.equipment_type_code)
    data: dict[str, Any] = body.model_dump(exclude={"equipment_type_code"})
    eq = Equipment(equipment_type_id=et.id, **data)
    db.add(eq)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("Equipment with this serial number already exists") from exc
    db.refresh(eq)
    return eq


def update_equipment(db: Session, eq: Equipment, body: EquipmentUpdate) -> Equipment:
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(eq, field, value)
    db.flush()
    db.refresh(eq)
    return eq
