"""Initial schema.

Written as explicit SQL so every table, constraint and index is visible and
reviewable in one place.

Revision ID: 0001
Revises:
Create Date: 2026-11-01
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE users (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email              varchar(320) NOT NULL,
    full_name          varchar(200) NOT NULL DEFAULT '',
    password_hash      varchar(255) NOT NULL,
    role               varchar(20)  NOT NULL CHECK (role IN ('admin', 'engineer', 'viewer')),
    is_active          boolean      NOT NULL DEFAULT true,
    failed_login_count integer      NOT NULL DEFAULT 0,
    locked_until       timestamptz,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT ck_users_email_lower CHECK (email = lower(email))
);
ALTER TABLE users RENAME CONSTRAINT users_pkey TO pk_users;

CREATE TABLE refresh_tokens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL,
    token_hash  varchar(64) NOT NULL,
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_refresh_tokens_token_hash UNIQUE (token_hash),
    CONSTRAINT fk_refresh_tokens_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
ALTER TABLE refresh_tokens RENAME CONSTRAINT refresh_tokens_pkey TO pk_refresh_tokens;
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);

CREATE TABLE equipment_types (
    id          serial PRIMARY KEY,
    code        varchar(50)  NOT NULL,
    name        varchar(100) NOT NULL,
    description text NOT NULL DEFAULT '',
    CONSTRAINT uq_equipment_types_code UNIQUE (code)
);
ALTER TABLE equipment_types RENAME CONSTRAINT equipment_types_pkey TO pk_equipment_types;

CREATE TABLE sensor_definitions (
    id                serial PRIMARY KEY,
    equipment_type_id integer NOT NULL,
    code              varchar(50)  NOT NULL,
    name              varchar(100) NOT NULL,
    unit              varchar(20)  NOT NULL,
    min_valid         double precision NOT NULL,
    max_valid         double precision NOT NULL,
    CONSTRAINT uq_sensor_definitions_type_code UNIQUE (equipment_type_id, code),
    CONSTRAINT ck_sensor_definitions_range CHECK (min_valid < max_valid),
    CONSTRAINT fk_sensor_definitions_equipment_type_id_equipment_types
        FOREIGN KEY (equipment_type_id) REFERENCES equipment_types (id) ON DELETE CASCADE
);
ALTER TABLE sensor_definitions RENAME CONSTRAINT sensor_definitions_pkey TO pk_sensor_definitions;

CREATE TABLE equipment (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    equipment_type_id    integer NOT NULL,
    name                 varchar(200) NOT NULL,
    serial_number        varchar(100) NOT NULL,
    manufacturer         varchar(100) NOT NULL DEFAULT '',
    model                varchar(100) NOT NULL DEFAULT '',
    install_date         date,
    location             varchar(200) NOT NULL DEFAULT '',
    operating_conditions jsonb NOT NULL DEFAULT '{}'::jsonb,
    status               varchar(20) NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'maintenance', 'retired')),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_equipment_serial_number UNIQUE (serial_number),
    CONSTRAINT fk_equipment_equipment_type_id_equipment_types
        FOREIGN KEY (equipment_type_id) REFERENCES equipment_types (id)
);
ALTER TABLE equipment RENAME CONSTRAINT equipment_pkey TO pk_equipment;
CREATE INDEX ix_equipment_equipment_type_id ON equipment (equipment_type_id);

-- Long/narrow time-series table, range-partitioned by month on ts.
-- Monthly partitions are created on demand by app.modules.ingestion.partitions.
CREATE TABLE sensor_readings (
    equipment_id uuid NOT NULL,
    sensor_id    integer NOT NULL,
    ts           timestamptz NOT NULL,
    value        double precision NOT NULL,
    CONSTRAINT pk_sensor_readings PRIMARY KEY (equipment_id, sensor_id, ts),
    CONSTRAINT fk_sensor_readings_equipment_id_equipment
        FOREIGN KEY (equipment_id) REFERENCES equipment (id) ON DELETE CASCADE,
    CONSTRAINT fk_sensor_readings_sensor_id_sensor_definitions
        FOREIGN KEY (sensor_id) REFERENCES sensor_definitions (id)
) PARTITION BY RANGE (ts);
CREATE TABLE sensor_readings_default PARTITION OF sensor_readings DEFAULT;
CREATE INDEX ix_sensor_readings_ts_brin ON sensor_readings USING brin (ts);

CREATE TABLE failure_events (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    equipment_id uuid NOT NULL,
    occurred_at  timestamptz NOT NULL,
    failure_mode varchar(50) NOT NULL,
    description  text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_failure_events_equipment_id_equipment
        FOREIGN KEY (equipment_id) REFERENCES equipment (id) ON DELETE CASCADE
);
ALTER TABLE failure_events RENAME CONSTRAINT failure_events_pkey TO pk_failure_events;
CREATE INDEX ix_failure_events_equipment_id ON failure_events (equipment_id);

CREATE TABLE maintenance_events (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    equipment_id     uuid NOT NULL,
    performed_at     timestamptz NOT NULL,
    maintenance_type varchar(20) NOT NULL
                     CHECK (maintenance_type IN ('inspection', 'repair', 'replacement', 'preventive')),
    notes            text NOT NULL DEFAULT '',
    cost             numeric(12, 2) CHECK (cost IS NULL OR cost >= 0),
    user_id          uuid,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_maintenance_events_equipment_id_equipment
        FOREIGN KEY (equipment_id) REFERENCES equipment (id) ON DELETE CASCADE,
    CONSTRAINT fk_maintenance_events_user_id_users
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
);
ALTER TABLE maintenance_events RENAME CONSTRAINT maintenance_events_pkey TO pk_maintenance_events;
CREATE INDEX ix_maintenance_events_equipment_id ON maintenance_events (equipment_id);

CREATE TABLE model_versions (
    id              serial PRIMARY KEY,
    name            varchar(100) NOT NULL,
    version         integer NOT NULL,
    algorithm       varchar(50) NOT NULL,
    horizon_days    integer NOT NULL,
    feature_names   jsonb NOT NULL,
    metrics         jsonb NOT NULL,
    artifact_uri    varchar(500) NOT NULL,
    artifact_sha256 varchar(64) NOT NULL,
    data_snapshot   jsonb NOT NULL,
    is_active       boolean NOT NULL DEFAULT false,
    trained_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_model_versions_name_version UNIQUE (name, version)
);
ALTER TABLE model_versions RENAME CONSTRAINT model_versions_pkey TO pk_model_versions;
CREATE UNIQUE INDEX uq_model_versions_one_active ON model_versions (name) WHERE is_active;

CREATE TABLE predictions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    equipment_id        uuid NOT NULL,
    model_version_id    integer NOT NULL,
    as_of               timestamptz NOT NULL,
    failure_probability double precision NOT NULL CHECK (failure_probability BETWEEN 0 AND 1),
    health_score        integer NOT NULL CHECK (health_score BETWEEN 0 AND 100),
    risk_level          varchar(10) NOT NULL CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    rul_days            double precision,
    anomaly_score       double precision,
    explanation         jsonb NOT NULL,
    recommendations     jsonb NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_predictions_equipment_id_equipment
        FOREIGN KEY (equipment_id) REFERENCES equipment (id) ON DELETE CASCADE,
    CONSTRAINT fk_predictions_model_version_id_model_versions
        FOREIGN KEY (model_version_id) REFERENCES model_versions (id)
);
ALTER TABLE predictions RENAME CONSTRAINT predictions_pkey TO pk_predictions;
CREATE INDEX ix_predictions_equipment_asof ON predictions (equipment_id, as_of DESC);

CREATE TABLE alerts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    equipment_id    uuid NOT NULL,
    prediction_id   uuid,
    previous_risk   varchar(10),
    new_risk        varchar(10) NOT NULL,
    status          varchar(20) NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'acknowledged', 'resolved')),
    message         text NOT NULL,
    acknowledged_by uuid,
    acknowledged_at timestamptz,
    resolved_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_alerts_equipment_id_equipment
        FOREIGN KEY (equipment_id) REFERENCES equipment (id) ON DELETE CASCADE,
    CONSTRAINT fk_alerts_prediction_id_predictions
        FOREIGN KEY (prediction_id) REFERENCES predictions (id) ON DELETE SET NULL,
    CONSTRAINT fk_alerts_acknowledged_by_users
        FOREIGN KEY (acknowledged_by) REFERENCES users (id) ON DELETE SET NULL
);
ALTER TABLE alerts RENAME CONSTRAINT alerts_pkey TO pk_alerts;
CREATE INDEX ix_alerts_equipment_id ON alerts (equipment_id);
CREATE INDEX ix_alerts_status ON alerts (status);

CREATE TABLE risk_thresholds (
    risk_level      varchar(10) PRIMARY KEY CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    min_probability double precision NOT NULL CHECK (min_probability BETWEEN 0 AND 1)
);
ALTER TABLE risk_thresholds RENAME CONSTRAINT risk_thresholds_pkey TO pk_risk_thresholds;
INSERT INTO risk_thresholds (risk_level, min_probability) VALUES
    ('low', 0.0), ('medium', 0.10), ('high', 0.30), ('critical', 0.60);

CREATE TABLE ingestion_batches (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source         varchar(10) NOT NULL CHECK (source IN ('api', 'csv')),
    filename       varchar(255),
    status         varchar(20) NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    rows_total     integer NOT NULL DEFAULT 0,
    rows_accepted  integer NOT NULL DEFAULT 0,
    rows_rejected  integer NOT NULL DEFAULT 0,
    rows_duplicate integer NOT NULL DEFAULT 0,
    errors         jsonb NOT NULL DEFAULT '[]'::jsonb,
    user_id        uuid,
    created_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    CONSTRAINT fk_ingestion_batches_user_id_users
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
);
ALTER TABLE ingestion_batches RENAME CONSTRAINT ingestion_batches_pkey TO pk_ingestion_batches;

CREATE TABLE jobs (
    id           bigserial PRIMARY KEY,
    job_type     varchar(50) NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    status       varchar(20) NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts     integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3,
    run_after    timestamptz NOT NULL DEFAULT now(),
    locked_at    timestamptz,
    finished_at  timestamptz,
    last_error   text,
    result       jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE jobs RENAME CONSTRAINT jobs_pkey TO pk_jobs;
CREATE INDEX ix_jobs_status_run_after ON jobs (status, run_after);

CREATE TABLE audit_log (
    id        bigserial PRIMARY KEY,
    user_id   uuid,
    action    varchar(100) NOT NULL,
    entity    varchar(50) NOT NULL,
    entity_id varchar(100),
    details   jsonb NOT NULL DEFAULT '{}'::jsonb,
    ts        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_audit_log_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
);
ALTER TABLE audit_log RENAME CONSTRAINT audit_log_pkey TO pk_audit_log;
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS audit_log, jobs, ingestion_batches, risk_thresholds, alerts, predictions,
    model_versions, maintenance_events, failure_events, sensor_readings, equipment,
    sensor_definitions, equipment_types, refresh_tokens, users CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
