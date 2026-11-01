import uuid

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, Admin, Engineer, Viewer
from app.core.errors import Conflict, NotFound
from app.db.models import EquipmentType, SensorDefinition
from app.modules import audit
from app.modules.equipment import service
from app.modules.equipment.schemas import (
    EquipmentCreate,
    EquipmentOut,
    EquipmentPage,
    EquipmentTypeIn,
    EquipmentTypeOut,
    EquipmentUpdate,
    SensorDefinitionIn,
    SensorDefinitionOut,
)

router = APIRouter(tags=["equipment"])


# ---- equipment types & sensors -------------------------------------------------
@router.get("/equipment-types", response_model=list[EquipmentTypeOut])
def list_types(db: DB, _: Viewer) -> list[EquipmentType]:
    return list(db.scalars(select(EquipmentType).order_by(EquipmentType.code)))


@router.post("/equipment-types", response_model=EquipmentTypeOut, status_code=201)
def create_type(body: EquipmentTypeIn, db: DB, admin: Admin) -> EquipmentType:
    et = EquipmentType(**body.model_dump())
    db.add(et)
    try:
        db.flush()
    except IntegrityError as exc:
        raise Conflict("Equipment type code already exists") from exc
    audit.record(db, admin.id, "equipment_type.create", "equipment_type", str(et.id), code=et.code)
    db.commit()
    db.refresh(et)
    return et


@router.post("/equipment-types/{type_id}/sensors", response_model=SensorDefinitionOut, status_code=201)
def add_sensor(type_id: int, body: SensorDefinitionIn, db: DB, admin: Admin) -> SensorDefinition:
    if db.get(EquipmentType, type_id) is None:
        raise NotFound("Equipment type not found")
    sd = SensorDefinition(equipment_type_id=type_id, **body.model_dump())
    db.add(sd)
    try:
        db.flush()
    except IntegrityError as exc:
        raise Conflict("Sensor code already exists for this equipment type") from exc
    audit.record(db, admin.id, "sensor.create", "sensor_definition", str(sd.id), code=sd.code)
    db.commit()
    return sd


# ---- equipment -----------------------------------------------------------------
@router.get("/equipment", response_model=EquipmentPage)
def list_equipment(
    db: DB,
    _: Viewer,
    type: str | None = None,
    status: str | None = None,
    risk: str | None = Query(default=None, pattern="^(low|medium|high|critical)$"),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> EquipmentPage:
    items, total = service.list_equipment(db, type_code=type, status=status, risk=risk, q=q, limit=limit, offset=offset)
    return EquipmentPage(items=items, total=total)


@router.post("/equipment", response_model=EquipmentOut, status_code=201)
def create_equipment(body: EquipmentCreate, db: DB, user: Engineer) -> EquipmentOut:
    eq = service.create_equipment(db, body)
    audit.record(db, user.id, "equipment.create", "equipment", str(eq.id), serial=eq.serial_number)
    db.commit()
    return service.to_out(eq, None)


@router.get("/equipment/{equipment_id}", response_model=EquipmentOut)
def get_equipment(equipment_id: uuid.UUID, db: DB, _: Viewer) -> EquipmentOut:
    eq = service.get_equipment(db, equipment_id)
    return service.to_out(eq, service.latest_predictions(db, [eq.id]).get(eq.id))


@router.patch("/equipment/{equipment_id}", response_model=EquipmentOut)
def update_equipment(equipment_id: uuid.UUID, body: EquipmentUpdate, db: DB, user: Engineer) -> EquipmentOut:
    eq = service.update_equipment(db, service.get_equipment(db, equipment_id), body)
    audit.record(db, user.id, "equipment.update", "equipment", str(eq.id), fields=sorted(body.model_fields_set))
    db.commit()
    return service.to_out(eq, service.latest_predictions(db, [eq.id]).get(eq.id))


@router.delete("/equipment/{equipment_id}", status_code=204)
def delete_equipment(equipment_id: uuid.UUID, db: DB, admin: Admin) -> None:
    """Hard delete (cascades readings and predictions). Admin only; prefer status='retired'."""
    eq = service.get_equipment(db, equipment_id)
    audit.record(db, admin.id, "equipment.delete", "equipment", str(eq.id), serial=eq.serial_number)
    db.delete(eq)
    db.commit()
