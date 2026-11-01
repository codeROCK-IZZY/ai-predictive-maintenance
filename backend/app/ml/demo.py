"""Load a synthetic fleet into the database (for demos, development and tests)."""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import log_extra
from app.db.models import Equipment, FailureEvent, MaintenanceEvent, SensorDefinition
from app.ml.synthetic import FleetConfig, SyntheticFleet, generate_fleet
from app.modules.equipment.service import get_type_by_code, seed_catalog
from app.modules.ingestion.storage import bulk_insert

logger = logging.getLogger(__name__)

CHUNK = 250_000


def load_fleet(db: Session, fleet: SyntheticFleet) -> dict[str, int]:
    """Insert units, readings, failures and maintenance. Skips units whose serial exists."""
    seed_catalog(db)
    sensor_ids: dict[tuple[str, str], int] = {}
    for sd in db.scalars(select(SensorDefinition)):
        sensor_ids[(sd.equipment_type.code, sd.code)] = sd.id
    eq_ids: dict[str, tuple] = {}
    for u in fleet.units:
        if db.scalar(select(Equipment.id).where(Equipment.serial_number == u.serial)):
            continue
        eq = Equipment(
            equipment_type_id=get_type_by_code(db, u.type_code).id,
            name=u.name,
            serial_number=u.serial,
            manufacturer=u.manufacturer,
            model=u.model,
            install_date=u.install_date,
            location=u.location,
            operating_conditions=u.operating_conditions,
        )
        db.add(eq)
        db.flush()
        eq_ids[u.serial] = (eq.id, u.type_code)

    r = fleet.readings[fleet.readings.serial.isin(eq_ids)]
    eq_col = r.serial.map(lambda s: eq_ids[s][0])
    sensor_col = [sensor_ids[(eq_ids[s][1], c)] for s, c in zip(r.serial, r.sensor, strict=True)]
    rows = list(zip(eq_col, sensor_col, r.ts.dt.to_pydatetime(), r.value.astype(float), strict=True))
    inserted = 0
    for i in range(0, len(rows), CHUNK):
        ins, _ = bulk_insert(db, rows[i : i + CHUNK])
        inserted += ins
    for f in fleet.failures.itertuples():
        if f.serial in eq_ids:
            db.add(
                FailureEvent(
                    equipment_id=eq_ids[f.serial][0],
                    occurred_at=f.occurred_at.to_pydatetime(),
                    failure_mode=f.failure_mode,
                    description="Synthetic failure event",
                )
            )
    for m in fleet.maintenance.itertuples():
        if m.serial in eq_ids:
            db.add(
                MaintenanceEvent(
                    equipment_id=eq_ids[m.serial][0],
                    performed_at=m.performed_at.to_pydatetime(),
                    maintenance_type=m.maintenance_type,
                    cost=Decimal(str(m.cost)),
                    notes=m.notes,
                )
            )
    db.flush()
    stats = {
        "equipment": len(eq_ids),
        "readings": inserted,
        "failures": int(fleet.failures.serial.isin(eq_ids).sum()),
        "maintenance_events": int(fleet.maintenance.serial.isin(eq_ids).sum()),
    }
    logger.info("synthetic fleet loaded", extra=log_extra(**stats))
    return stats


def load_demo(db: Session, config: FleetConfig | None = None) -> dict[str, int]:
    return load_fleet(db, generate_fleet(config))
