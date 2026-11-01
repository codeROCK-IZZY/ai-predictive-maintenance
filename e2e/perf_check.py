"""Single-client throughput/latency check against a running stack (not a load test).

Usage: E2E_PASSWORD=... python e2e/perf_check.py   (creates and deletes a temporary unit)
"""
import time, statistics, json, urllib.request, datetime as dt
import os
B = os.environ.get("API_URL", "http://localhost:8000/api/v1")
def req(path, body=None, token=None, method=None):
    r = urllib.request.Request(B + path, data=None if body is None else json.dumps(body).encode(),
        method=method or ("POST" if body is not None else "GET"), headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(r) as resp: return json.loads(resp.read() or b"null")
tok = req("/auth/login", {"email": "admin@example.com", "password": os.environ["E2E_PASSWORD"]})["access_token"]
eq = req("/equipment", {"name": "Perf pump", "serial_number": f"PERF-{int(time.time())}", "equipment_type_code": "pump"}, tok)
t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc); sensors = ["vibration_rms","bearing_temp","motor_current","discharge_pressure","flow_rate"]
total, start = 0, time.perf_counter()
for b in range(10):
    rows = [{"sensor": sensors[i % 5], "ts": (t0 + dt.timedelta(minutes=b * 2000 + i // 5)).isoformat(), "value": 5.0} for i in range(10_000)]
    total += req(f"/equipment/{eq['id']}/readings:batch", {"readings": rows}, tok)["rows_accepted"]
el = time.perf_counter() - start
print(f"ingestion: {total} readings in {el:.2f}s = {total/el:,.0f} readings/s (REST, 10k-row batches, incl. JSON parsing + validation)")
ids = [e["id"] for e in req("/equipment?limit=20&type=compressor", token=tok)["items"]]
lat = []
for i in range(40):
    s = time.perf_counter(); req(f"/equipment/{ids[i % len(ids)]}/predict", {}, tok); lat.append((time.perf_counter() - s) * 1000)
lat.sort(); print(f"predict (sync, incl. DB read of 33 days + features + SHAP + write): p50 {statistics.median(lat):.0f} ms, p95 {lat[int(0.95*len(lat))-1]:.0f} ms, n={len(lat)}")
req(f"/equipment/{eq['id']}", None, tok, method="DELETE")
