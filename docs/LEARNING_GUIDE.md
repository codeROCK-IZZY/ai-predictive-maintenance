# Learning guide: understanding this codebase

Work through the stops in this order. Each one names the files, explains the key ideas, and ends with exercises. Try each exercise before reading the hint.

---

## 1. One request, end to end
**Read:** `app/main.py` → `app/api/deps.py` → `app/modules/equipment/router.py` → `service.py`

Every HTTP request passes through the `request_context` middleware, which assigns a request ID, adds security headers and logs the request. Then it reaches a router function. The router declares its dependencies:
- `db: DB` gives one SQLAlchemy session per request (`get_db`), which is rolled back if nothing commits.
- `user: Engineer` decodes the JWT, loads the user and checks the role (`require_role`).

The router validates input with Pydantic schemas, calls the service, and commits. Errors raised anywhere as `AppError` subclasses become RFC 9457 JSON (`app/core/errors.py`).

**Exercises**
1. Without running anything, predict the HTTP status for: a viewer calling `POST /api/v1/equipment`; an engineer posting `install_date` in the future; a duplicate serial number. Then check `tests/test_users_equipment.py`.
2. Add a `GET /api/v1/equipment/{id}/summary` endpoint returning the name and the number of readings. Write the test first.

> Hint: count with `SELECT count(*) FROM sensor_readings WHERE equipment_id = :e`.

---

## 2. Authentication
**Read:** `app/core/security.py`, `app/modules/auth/service.py`

- **Passwords** are hashed with argon2id. `verify_password` never raises; it returns `False`.
- **Login** spends the same time on unknown e-mails (`DUMMY_HASH`), so attackers can't tell which accounts exist from response times.
- **Refresh tokens** are random strings; only their SHA-256 is stored. Each refresh *rotates* the token. Presenting an already-rotated token means it was stolen and replayed, so every token for that user is revoked.

**Exercises**
1. Why is the access token kept in JavaScript memory, and the refresh token in an httpOnly cookie? What attack does each placement defend against?
2. Explain line by line what `rotate_refresh_token` does when `token.revoked_at is not None`.

---

## 3. Storing time series
**Read:** `migrations/versions/0001_initial_schema.py` (the `sensor_readings` table), `app/modules/ingestion/storage.py`

`bulk_insert` makes sure the monthly partitions exist, `COPY`s rows into a temporary staging table, then runs `INSERT … SELECT DISTINCT ON … ON CONFLICT DO NOTHING`. Duplicates are the difference between rows staged and rows inserted.

**Exercises**
1. Why can't `COPY` write straight into `sensor_readings` here?
2. What would happen if a reading arrived for a month whose partition did not exist? (Look at the `DEFAULT` partition.)
3. In `psql`, run `EXPLAIN ANALYZE` on a query for one month of one unit's readings, and find the evidence of partition pruning.

---

## 4. The job queue
**Read:** `app/jobs/queue.py`, `app/jobs/worker.py`

`claim_next` runs a single `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1)`. Two workers can never claim the same job, and neither waits for the other.

**Exercises**
1. `tests/test_jobs_schema.py::test_skip_locked_two_workers_never_get_same_job` simulates two workers. Explain why worker 2 doesn't block.
2. A bug was found and fixed here: `db.get()` returned a *stale cached* `Job` after the raw SQL `UPDATE`. Why does `populate_existing=True` fix it?

---

## 5. Features: the heart of the ML
**Read:** `app/ml/features.py`

- **`model_signals`** converts temperatures to *rise above ambient*. Without it, models learned seasonal patterns (ambient temperature was a top feature) that don't cause failures.
- **Baseline:** for each signal, it's the 30-day rolling median and std, **shifted back 72 h**, so a fault that's developing doesn't contaminate its own baseline.
- **`z_24h`** = (24 h mean − baseline) / baseline std. It means "how unusual for *this* unit", which is why one model works across units with different normal levels.
- **`_rolling_slope`** is a least-squares slope computed with rolling sums. It works even with missing values.

**Exercises**
1. Create a series that is flat at 2.0 for 30 days, then rises linearly by 0.1 per hour for a day. Compute features and check `z_24h` and `slope_24h`. Do they match your hand calculation?
2. What breaks if `BASELINE_GAP` is 0? Change it, re-run `python -m app.cli evaluate --units 20 --days 180`, and compare.

---

## 6. Labels and honest evaluation
**Read:** `app/ml/dataset.py`, `app/ml/training.py::evaluate`

- **Label:** a failure within the next 7 days. The last 7 days of data are dropped because we can't know their labels.
- **`GroupKFold` by unit:** observations from the same unit, 6 hours apart, are nearly identical. Random splitting would leak them into the test set and inflate the scores.
- **The time holdout is purged:** training stops 7 days before the cutoff, so no training label "sees" test-period failures.

**Exercises**
1. Change `GroupKFold` to an ordinary `KFold(shuffle=True)` and re-run the evaluation. How much does PR-AUC go up? Explain why that number would be a lie.
2. Why is PR-AUC the primary metric rather than accuracy? What accuracy does a model get by always answering "no failure"?

---

## 7. Explanations and recommendations
**Read:** `app/ml/training.py::contributions`, `app/ml/explain.py::top_factors`, `app/ml/recommendations.py`

For logistic regression, the SHAP value of a feature is `coefficient × standardised value`. The test `test_linear_contributions_sum_to_logit` proves that the values sum to the model's logit. Correlated features can receive large *opposite* contributions, so `top_factors` sums them per sensor ("grouped SHAP").

**Exercises**
1. Using the details shown in a prediction's `explanation.top_factors[i].details`, find a case where two features of the same sensor have opposite signs.
2. Add a rule to `recommend()`: if `anomaly_score > 0.995` **and** the risk is high or critical, add "Verify the reading is not a sensor fault before shutting down." Write the test first.

---

## 8. Frontend
**Read:** `frontend/src/api/client.ts`, `src/lib/auth.tsx`, `src/pages/EquipmentDetailPage.tsx`

`api()` attaches the token. On a 401 it refreshes once and retries; if that fails, the user is logged out. A test caught a bug where a refresh result stayed cached and was reused after it had already finished.

**Exercises**
1. Add a "Last 7 days of alerts" panel to the equipment page using `GET /api/v1/alerts?equipment_id=…`.
2. Why does the health gauge use `healthStroke()` instead of `healthColor().replace("bg-", "stroke-")`? (Hint: how does Tailwind decide which CSS to generate?)

---

## Check yourself
You understand the system when you can, without notes:
1. draw the path of a CSV upload from the browser to rows in `sensor_readings`, naming every process involved (local and AWS);
2. explain why the model's "34 of 35 failures detected" result does not mean it will catch 97 % of real failures;
3. roll back to the previous model version, and describe what happens to existing predictions.
