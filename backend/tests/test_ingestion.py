from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import new_session
from app.jobs.worker import drain
from tests.conftest import make_equipment

T0 = datetime(2026, 6, 1, tzinfo=UTC)


def _batch(client, h, eq_id, readings):
    return client.post(f"/api/v1/equipment/{eq_id}/readings:batch", json={"readings": readings}, headers=h)


def test_batch_ingest_valid_and_invalid_rows(client, auth):
    h = auth("engineer")
    eq = make_equipment(client, h)
    readings = [
        {"sensor": "vibration_rms", "ts": (T0 + timedelta(minutes=i)).isoformat(), "value": 2.0 + i / 100}
        for i in range(50)
    ] + [
        {"sensor": "rpm", "ts": T0.isoformat(), "value": 1500},  # pumps have no rpm sensor
        {"sensor": "vibration_rms", "ts": T0.isoformat(), "value": 999},  # outside valid range
        {"sensor": "vibration_rms", "ts": (datetime.now(UTC) + timedelta(days=1)).isoformat(), "value": 2},  # future
    ]
    r = _batch(client, h, eq["id"], readings)
    assert r.status_code == 202, r.text
    b = r.json()
    assert (b["rows_total"], b["rows_accepted"], b["rows_rejected"], b["rows_duplicate"]) == (53, 50, 3, 0)
    msgs = " ".join(e["error"] for e in b["errors"])
    assert "unknown sensor" in msgs and "outside valid range" in msgs and "future" in msgs
    with new_session() as s:
        assert s.execute(text("SELECT count(*) FROM sensor_readings")).scalar() == 50
        # Monthly partition created and used; default partition stays empty.
        assert s.execute(text("SELECT count(*) FROM sensor_readings_y2026m06")).scalar() == 50
        assert s.execute(text("SELECT count(*) FROM sensor_readings_default")).scalar() == 0


def test_duplicates_are_skipped_and_counted(client, auth):
    h = auth("engineer")
    eq = make_equipment(client, h)
    rows = [{"sensor": "flow_rate", "ts": T0.isoformat(), "value": 100.0}]
    assert _batch(client, h, eq["id"], rows).json()["rows_accepted"] == 1
    again = _batch(client, h, eq["id"], rows * 2).json()
    assert again["rows_accepted"] == 0 and again["rows_duplicate"] == 2


def test_naive_timestamp_and_empty_batch_rejected(client, auth):
    h = auth("engineer")
    eq = make_equipment(client, h)
    assert (
        _batch(client, h, eq["id"], [{"sensor": "flow_rate", "ts": "2026-06-01T00:00:00", "value": 1}]).status_code
        == 422
    )
    assert _batch(client, h, eq["id"], []).status_code == 422


def test_batch_size_limit(client, auth, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_batch_readings", 2)
    h = auth("engineer")
    eq = make_equipment(client, h)
    rows = [{"sensor": "flow_rate", "ts": (T0 + timedelta(minutes=i)).isoformat(), "value": 1} for i in range(3)]
    assert _batch(client, h, eq["id"], rows).status_code == 422


def test_batch_unknown_equipment(client, auth):
    import uuid

    r = _batch(client, auth("engineer"), uuid.uuid4(), [{"sensor": "x", "ts": T0.isoformat(), "value": 1}])
    assert r.status_code == 404


# ---------------------------------------------------------------- CSV
def _upload(client, h, content: bytes, name="data.csv", ctype="text/csv"):
    return client.post("/api/v1/ingestion/csv", files={"file": (name, content, ctype)}, headers=h)


def test_csv_import_end_to_end(client, auth):
    h = auth("engineer")
    make_equipment(client, h, "PUM-1")
    lines = ["﻿equipment_serial,sensor,ts,value"]  # BOM, as Excel writes it
    lines += [f"PUM-1,discharge_pressure,{(T0 + timedelta(minutes=10 * i)).isoformat()},6.{i % 10}" for i in range(30)]
    lines += ["NOPE-9,flow_rate,2026-06-01T00:00:00Z,1", "PUM-1,flow_rate,not-a-date,1", "PUM-1,flow_rate,,abc"]
    r = _upload(client, h, "\n".join(lines).encode())
    assert r.status_code == 202, r.text
    batch_id = r.json()["id"]
    assert r.json()["status"] == "pending"
    assert drain() >= 1  # run the background worker
    b = client.get(f"/api/v1/ingestion/batches/{batch_id}", headers=h).json()
    assert b["status"] == "completed"
    assert (b["rows_total"], b["rows_accepted"], b["rows_rejected"]) == (33, 30, 3)
    assert any("unknown equipment serial" in e["error"] for e in b["errors"])
    # The raw upload is deleted after import.
    assert not list(get_settings().upload_dir.glob(f"{batch_id}*"))


def test_csv_rejects_bad_header_extension_and_size(client, auth, monkeypatch):
    h = auth("engineer")
    assert _upload(client, h, b"a,b,c\n1,2,3").status_code == 422
    assert _upload(client, h, b"equipment_serial,sensor,ts,value\n", name="data.xlsx").status_code == 422
    assert _upload(client, h, b"equipment_serial,sensor,ts,value\n", ctype="image/png").status_code == 422
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    big = b"equipment_serial,sensor,ts,value\n" + b"PUM-1,flow_rate,2026-06-01T00:00:00Z,1\n" * 200_000
    assert _upload(client, h, big).status_code == 422
    assert not list(get_settings().upload_dir.glob("*.csv"))


# ---------------------------------------------------------------- readings query
def test_readings_downsampling(client, auth):
    h = auth("engineer")
    eq = make_equipment(client, h)
    rows = [
        {"sensor": "vibration_rms", "ts": (T0 + timedelta(minutes=10 * i)).isoformat(), "value": float(i % 6)}
        for i in range(6 * 24)
    ]  # 24 h of 10-minute data
    _batch(client, h, eq["id"], rows)
    q = f"/api/v1/equipment/{eq['id']}/readings?sensor=vibration_rms&from={T0.isoformat()}"
    q += f"&to={(T0 + timedelta(days=1)).isoformat()}"
    hourly = client.get(q.replace("+", "%2B") + "&bucket=1h", headers=auth("viewer")).json()
    assert len(hourly) == 1 and hourly[0]["unit"] == "mm/s"
    pts = hourly[0]["points"]
    assert len(pts) == 24 and pts[0]["count"] == 6
    assert (pts[0]["min"], pts[0]["max"], pts[0]["avg"]) == (0.0, 5.0, 2.5)
    daily = client.get(q.replace("+", "%2B") + "&bucket=1d", headers=h).json()[0]["points"]
    assert sum(p["count"] for p in daily) == 144
    raw = client.get(q.replace("+", "%2B") + "&bucket=raw", headers=h).json()[0]["points"]
    assert len(raw) == 144


def test_readings_validation(client, auth):
    h = auth("engineer")
    eq = make_equipment(client, h)
    assert client.get(f"/api/v1/equipment/{eq['id']}/readings?sensor=rpm", headers=h).status_code == 422
    assert client.get(f"/api/v1/equipment/{eq['id']}/readings?bucket=5s", headers=h).status_code == 422


def test_csv_handoff_through_s3_when_bucket_configured(client, auth, monkeypatch, tmp_path):
    """On AWS the API and worker don't share a disk; the upload goes through S3."""
    from app.modules.ingestion import uploads

    store: dict[str, bytes] = {}

    class FakeS3:
        def upload_file(self, filename, bucket, key, ExtraArgs=None):
            assert bucket == "bkt" and ExtraArgs == {"ServerSideEncryption": "AES256"}
            store[key] = Path(filename).read_bytes()

        def download_file(self, bucket, key, filename):
            Path(filename).write_bytes(store[key])

        def delete_object(self, Bucket, Key):
            del store[Key]

    monkeypatch.setattr(get_settings(), "artifact_s3_bucket", "bkt")
    monkeypatch.setattr(uploads, "_s3", lambda: FakeS3())
    h = auth("engineer")
    make_equipment(client, h, "PUM-1")
    body = "equipment_serial,sensor,ts,value\nPUM-1,flow_rate,2026-06-01T00:00:00Z,100\n"
    r = _upload(client, h, body.encode())
    assert r.status_code == 202
    assert list(store) == [f"uploads/{r.json()['id']}.csv"]  # staged in S3, not on local disk
    assert not list(get_settings().upload_dir.glob("*.csv"))
    drain()
    assert client.get(f"/api/v1/ingestion/batches/{r.json()['id']}", headers=h).json()["rows_accepted"] == 1
    assert store == {}  # object removed after import


def test_upload_fetch_rejects_untrusted_locations(monkeypatch):
    import pytest

    from app.modules.ingestion import uploads

    with pytest.raises(ValueError), uploads.fetch({"path": "/etc/passwd"}):
        pass
    monkeypatch.setattr(get_settings(), "artifact_s3_bucket", "bkt")
    with pytest.raises(ValueError), uploads.fetch({"s3_key": "models/../secret"}):
        pass
