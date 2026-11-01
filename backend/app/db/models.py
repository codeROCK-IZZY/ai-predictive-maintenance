"""SQLAlchemy ORM models.

The schema itself is created by the Alembic migration in ``migrations/versions``
(the source of truth). ``tests/test_schema.py`` checks that these models and the
migrated database agree.

``sensor_readings`` is a range-partitioned table, so it is declared here as a
Core ``Table`` used for queries only.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Double,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ROLES = ("admin", "engineer", "viewer")
RISK_LEVELS = ("low", "medium", "high", "critical")
EQUIPMENT_STATUSES = ("active", "maintenance", "retired")
MAINTENANCE_TYPES = ("inspection", "repair", "replacement", "preventive")
ALERT_STATUSES = ("open", "acknowledged", "resolved")
JOB_STATUSES = ("queued", "running", "succeeded", "failed")

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


class Base(DeclarativeBase):
    metadata = metadata


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class EquipmentType(Base):
    __tablename__ = "equipment_types"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    sensors: Mapped[list["SensorDefinition"]] = relationship(
        back_populates="equipment_type", order_by="SensorDefinition.id", lazy="selectin"
    )


class SensorDefinition(Base):
    __tablename__ = "sensor_definitions"
    __table_args__ = (UniqueConstraint("equipment_type_id", "code", name="uq_sensor_definitions_type_code"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    equipment_type_id: Mapped[int] = mapped_column(ForeignKey("equipment_types.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    min_valid: Mapped[float] = mapped_column(Double, nullable=False)
    max_valid: Mapped[float] = mapped_column(Double, nullable=False)
    equipment_type: Mapped[EquipmentType] = relationship(back_populates="sensors")


class Equipment(Base):
    __tablename__ = "equipment"
    id: Mapped[uuid.UUID] = _uuid_pk()
    equipment_type_id: Mapped[int] = mapped_column(ForeignKey("equipment_types.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    serial_number: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    manufacturer: Mapped[str] = mapped_column(String(100), nullable=False, server_default="")
    model: Mapped[str] = mapped_column(String(100), nullable=False, server_default="")
    install_date: Mapped[date | None] = mapped_column(Date)
    location: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    operating_conditions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    equipment_type: Mapped[EquipmentType] = relationship(lazy="joined")


sensor_readings = Table(
    "sensor_readings",
    metadata,
    Column("equipment_id", UUID(as_uuid=True), ForeignKey("equipment.id", ondelete="CASCADE"), primary_key=True),
    Column("sensor_id", Integer, ForeignKey("sensor_definitions.id"), primary_key=True),
    Column("ts", DateTime(timezone=True), primary_key=True),
    Column("value", Double, nullable=False),
    Index("ix_sensor_readings_ts_brin", "ts", postgresql_using="brin"),
    postgresql_partition_by="RANGE (ts)",
)


class FailureEvent(Base):
    __tablename__ = "failure_events"
    id: Mapped[uuid.UUID] = _uuid_pk()
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failure_mode: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = _created()


class MaintenanceEvent(Base):
    __tablename__ = "maintenance_events"
    id: Mapped[uuid.UUID] = _uuid_pk()
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    maintenance_type: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_model_versions_name_version"),
        Index(
            "uq_model_versions_one_active",
            "name",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(50), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_names: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    artifact_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    trained_at: Mapped[datetime] = _created()


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (Index("ix_predictions_equipment_asof", "equipment_id", text("as_of DESC")),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failure_probability: Mapped[float] = mapped_column(Float, nullable=False)
    health_score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False)
    rul_days: Mapped[float | None] = mapped_column(Float)
    anomaly_score: Mapped[float | None] = mapped_column(Float)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recommendations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created()


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[uuid.UUID] = _uuid_pk()
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prediction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("predictions.id", ondelete="SET NULL")
    )
    previous_risk: Mapped[str | None] = mapped_column(String(10))
    new_risk: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open", index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class RiskThreshold(Base):
    __tablename__ = "risk_thresholds"
    risk_level: Mapped[str] = mapped_column(String(10), primary_key=True)
    min_probability: Mapped[float] = mapped_column(Float, nullable=False)


class IngestionBatch(Base):
    __tablename__ = "ingestion_batches"
    id: Mapped[uuid.UUID] = _uuid_pk()
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rows_accepted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rows_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
