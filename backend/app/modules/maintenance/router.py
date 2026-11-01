"""Maintenance log and failure records. Both feed back into model training:
maintenance resets ``days_since_service``; failures are the training labels."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.api.deps import DB, Engineer, Viewer
from app.db.models import FailureEvent, MaintenanceEvent
from app.modules import audit
from app.modules.equipment.service import get_equipment

router = APIRouter(tags=["maintenance"])


def _not_future(v: datetime) -> datetime:
    if v > datetime.now(UTC):
        raise ValueError("date cannot be in the future")
    return v


class MaintenanceIn(BaseModel):
    performed_at: AwareDatetime
    maintenance_type: Literal["inspection", "repair", "replacement", "preventive"]
    notes: str = Field(default="", max_length=5000)
    cost: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)

    _v = field_validator("performed_at")(_not_future)


class MaintenanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    equipment_id: uuid.UUID
    performed_at: datetime
    maintenance_type: str
    notes: str
    cost: Decimal | None
    user_id: uuid.UUID | None
    created_at: datetime


class FailureIn(BaseModel):
    occurred_at: AwareDatetime
    failure_mode: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(default="", max_length=5000)

    _v = field_validator("occurred_at")(_not_future)


class FailureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    equipment_id: uuid.UUID
    occurred_at: datetime
    failure_mode: str
    description: str
    created_at: datetime


@router.get("/equipment/{equipment_id}/maintenance", response_model=list[MaintenanceOut])
def list_maintenance(
    equipment_id: uuid.UUID, db: DB, _: Viewer, limit: int = Query(default=100, ge=1, le=1000)
) -> list[MaintenanceEvent]:
    get_equipment(db, equipment_id)
    return list(
        db.scalars(
            select(MaintenanceEvent)
            .where(MaintenanceEvent.equipment_id == equipment_id)
            .order_by(MaintenanceEvent.performed_at.desc())
            .limit(limit)
        )
    )


@router.post("/equipment/{equipment_id}/maintenance", response_model=MaintenanceOut, status_code=201)
def add_maintenance(equipment_id: uuid.UUID, body: MaintenanceIn, db: DB, user: Engineer) -> MaintenanceEvent:
    get_equipment(db, equipment_id)
    ev = MaintenanceEvent(equipment_id=equipment_id, user_id=user.id, **body.model_dump())
    db.add(ev)
    db.commit()
    return ev


@router.get("/equipment/{equipment_id}/failures", response_model=list[FailureOut])
def list_failures(equipment_id: uuid.UUID, db: DB, _: Viewer) -> list[FailureEvent]:
    get_equipment(db, equipment_id)
    return list(
        db.scalars(
            select(FailureEvent)
            .where(FailureEvent.equipment_id == equipment_id)
            .order_by(FailureEvent.occurred_at.desc())
        )
    )


@router.post("/equipment/{equipment_id}/failures", response_model=FailureOut, status_code=201)
def add_failure(equipment_id: uuid.UUID, body: FailureIn, db: DB, user: Engineer) -> FailureEvent:
    get_equipment(db, equipment_id)
    ev = FailureEvent(equipment_id=equipment_id, **body.model_dump())
    db.add(ev)
    db.flush()
    audit.record(db, user.id, "failure.record", "failure_event", str(ev.id), equipment_id=str(equipment_id))
    db.commit()
    return ev
