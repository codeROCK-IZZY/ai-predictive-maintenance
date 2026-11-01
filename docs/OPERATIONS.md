# Operations runbook

## Health checks
- `GET /api/health/live` — process is up (used by container and load-balancer health checks).
- `GET /api/health/ready` — database reachable and whether a model is active.

## Logs
JSON lines on stdout with `request_id` (also returned as the `X-Request-ID` response header — ask users for it when they report an error). Locally: `docker compose logs -f api worker`. On AWS: CloudWatch log groups `/ecs/pdm/{api,worker,web}`.

Useful CloudWatch Logs Insights query:
```
fields ts, level, msg, path, status, ms, request_id | filter level = "ERROR" or status >= 500 | sort ts desc
```

## Background jobs
Jobs live in the `jobs` table. Check them in the UI (Models page shows training jobs) or SQL:
```sql
SELECT id, job_type, status, attempts, left(last_error, 200), created_at FROM jobs ORDER BY id DESC LIMIT 20;
```
Failed jobs retry with backoff (30 s, 60 s, …) up to `max_attempts`. Jobs stuck in `running` for more than 30 minutes (e.g. worker crashed) are re-queued automatically by the worker.

## Model lifecycle
| Task | How |
|---|---|
| Train a new version | Models page → *Train new model*, or `python -m app.cli train` |
| Review it | Models page: CV and holdout metrics, threshold table, calibration, top features |
| Activate | Admin → *Activate* (re-scores all equipment). Trained-from-UI models activate automatically; use `activate: false` in `POST /models/train` to review first |
| **Roll back** | Activate the previous version on the Models page. Artifacts are kept and integrity-checked on load |
| Re-tune risk thresholds | Administration → Risk thresholds (applies to new predictions) |

Retrain when: significant new failure history has been recorded, new equipment types are added, or the alert precision observed by engineers drops.

## Backups and recovery
- **Local (compose):** `docker compose exec db pg_dump -U pdm -Fc pdm > backup.dump`; restore with `pg_restore -U pdm -d pdm --clean backup.dump`. Model artifacts are in the `artifacts` volume (`docker run --rm -v predictive-maintenance_artifacts:/v -v $PWD:/b alpine tar czf /b/artifacts.tgz /v`).
- **AWS:** RDS automated backups (7 days, point-in-time recovery) and a final snapshot on deletion; S3 artifact bucket is versioned. Restore = new RDS instance from snapshot/PITR, then point the `database-url` secret at it and redeploy.

## Data retention
Readings are partitioned by month (`sensor_readings_yYYYYmMM`). To drop data older than a month you no longer need:
```sql
DROP TABLE sensor_readings_y2025m01;   -- instant, no table bloat
```
Keep at least ~33 days of history per unit for features.

## Troubleshooting
| Symptom | Likely cause / fix |
|---|---|
| "No active model" (503) | Train and activate a model |
| "Model artifact failed integrity check" (503) | Artifact file changed or corrupted. Re-activate another version or retrain |
| CSV import stuck in `pending` | Worker not running: `docker compose ps worker`, check its logs |
| Prediction shows "stale data" | No readings for that unit in the last 6 h. Check the data source |
| Login always fails for one user | Account locked for 15 min after 5 failed attempts |
