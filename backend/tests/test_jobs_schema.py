from datetime import UTC, datetime, timedelta

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from app.db.models import Job, metadata
from app.db.session import get_engine, new_session
from app.jobs import handlers, queue, worker


# ---------------------------------------------------------------- schema
def test_orm_models_match_migrated_schema(clean_db):
    def include(obj, name, type_, reflected, compare_to):
        return not (type_ == "table" and name and (name.startswith("sensor_readings_") or name == "alembic_version"))

    with get_engine().connect() as conn:
        mc = MigrationContext.configure(conn, opts={"include_object": include, "compare_type": True})
        assert compare_metadata(mc, metadata) == []


def test_database_constraints(db):
    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO users (email, password_hash, role) VALUES ('A@x.com', 'h', 'viewer')"))
    db.rollback()
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO users (email, password_hash, role) VALUES ('a@x.com', 'h', 'root')"))


# ---------------------------------------------------------------- queue
def test_enqueue_claim_succeed(db):
    job = queue.enqueue(db, "noop", {"a": 1})
    db.commit()
    claimed = queue.claim_next(db)
    assert claimed is not None and claimed.id == job.id and claimed.status == "running" and claimed.attempts == 1
    assert queue.claim_next(db) is None  # nothing else runnable
    queue.mark_succeeded(db, claimed, {"ok": True})
    db.refresh(claimed)
    assert claimed.status == "succeeded" and claimed.result == {"ok": True}


def test_dedupe(db):
    a = queue.enqueue(db, "score_equipment", {"equipment_id": "x"}, dedupe=True)
    b = queue.enqueue(db, "score_equipment", {"equipment_id": "x"}, dedupe=True)
    c = queue.enqueue(db, "score_equipment", {"equipment_id": "y"}, dedupe=True)
    assert a.id == b.id != c.id


def test_retry_with_backoff_then_failed(db, monkeypatch):
    calls = []

    def boom(_db, payload):
        calls.append(payload)
        raise RuntimeError("transient failure")

    monkeypatch.setitem(handlers.HANDLERS, "flaky", boom)
    job = queue.enqueue(db, "flaky", {}, max_attempts=2)
    db.commit()
    assert worker.run_one(db)
    db.refresh(job)
    assert job.status == "queued" and job.attempts == 1 and "transient failure" in job.last_error
    assert job.run_after > datetime.now(UTC) + timedelta(seconds=20)  # backoff
    assert not worker.run_one(db)  # not runnable yet
    job.run_after = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert worker.run_one(db)
    db.refresh(job)
    assert job.status == "failed" and job.attempts == 2 and len(calls) == 2


def test_unknown_job_type_fails(db):
    job = queue.enqueue(db, "does_not_exist", {}, max_attempts=1)
    db.commit()
    worker.run_one(db)
    db.refresh(job)
    assert job.status == "failed" and "no handler" in job.last_error


def test_skip_locked_two_workers_never_get_same_job(db):
    for i in range(2):
        queue.enqueue(db, "noop", {"i": i})
    db.commit()
    s1, s2 = new_session(), new_session()
    try:
        # Worker 1 holds a row lock inside an open transaction...
        row1 = s1.execute(
            text("SELECT id FROM jobs WHERE status='queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1")
        ).scalar()
        # ...worker 2 skips it and gets the other one, without blocking.
        claimed = queue.claim_next(s2)
        assert claimed is not None and claimed.id != row1
    finally:
        s1.rollback()
        s1.close()
        s2.close()


def test_requeue_stale_jobs(db):
    job = Job(job_type="noop", payload={}, status="running", locked_at=datetime.now(UTC) - timedelta(hours=2))
    db.add(job)
    db.commit()
    assert queue.requeue_stale(db) == 1
    db.refresh(job)
    assert job.status == "queued"
