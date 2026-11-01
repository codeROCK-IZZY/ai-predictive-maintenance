# Development guide

## Prerequisites
- Python 3.12+ (developed on 3.13), Node 22, PostgreSQL 16 (or Docker)

## Backend without Docker

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
```

Create a database and user (any PostgreSQL 16):

```bash
psql -U postgres -c "CREATE ROLE pdm LOGIN PASSWORD 'pdm' CREATEDB;" -c "CREATE DATABASE pdm OWNER pdm;" -c "CREATE DATABASE pdm_test OWNER pdm;"
```

Configure your environment. Settings are read from environment variables or from `backend/.env`:

```bash
export PDM_DATABASE_URL=postgresql+psycopg://pdm:pdm@localhost:5432/pdm
export PDM_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
export PDM_COOKIE_SECURE=false PDM_LOG_JSON=false
export PDM_ADMIN_PASSWORD='choose-a-long-password'
```

```bash
python -m app.cli demo --admin-email admin@example.com
```

```bash
uvicorn app.main:app --reload --port 8000
```

In a second terminal, start the background worker:

```bash
python -m app.cli worker
```

## Frontend

```bash
cd frontend && npm install && npm run dev
```

Vite serves <http://localhost:5173> and proxies `/api` to `localhost:8000`, so cookies stay same-origin.

## Tests

| Command | What it runs |
|---|---|
| `cd backend && pytest` | 124 tests against the real `pdm_test` database. The schema is dropped and rebuilt from the Alembic migration at the start of each run. |
| `cd backend && pytest -m "not slow"` | Skips `test_ml_pipeline.py`, which trains models (about 50 s) |
| `cd backend && pytest --cov=app` | With coverage |
| `cd frontend && npm test` | Vitest + Testing Library |
| `make smoke E2E_PASSWORD=...` | Playwright browser smoke test against a running stack |
| `python e2e/perf_check.py` | Single-client throughput/latency check |

Override the test database with `PDM_DATABASE_URL=...` if yours differs.

## CLI reference (`python -m app.cli <command>`)

| Command | Purpose |
|---|---|
| `migrate` | Apply Alembic migrations |
| `create-admin --email E` | Create an admin (password from `PDM_ADMIN_PASSWORD` or prompt) |
| `seed-catalog` | Insert default equipment types and sensors |
| `load-demo [--units --days --seed]` | Generate and load a synthetic fleet |
| `export-sample-csv --out F` | Write a small CSV in the upload format |
| `train [--algorithm auto\|logistic_regression\|xgboost] [--no-compare]` | Train, evaluate, register and activate a model |
| `evaluate --out DIR` | Offline evaluation on an in-memory fleet; writes `evaluation.json` and `.md` |
| `score-all` | Score every unit with the active model |
| `backfill [--days]` | Replay the active model over recent history (risk trend and alerts) |
| `worker` | Run the background job worker |
| `demo` | All of the above in order, for a one-command setup |

## Database migrations
The schema lives in `backend/migrations/versions/` as explicit SQL. To change it:
1. Edit `app/db/models.py`.
2. Add a new revision with `alembic revision -m "describe change"` and write the `upgrade()`/`downgrade()` SQL.
3. Run `pytest tests/test_jobs_schema.py::test_orm_models_match_migrated_schema`. It fails if the models and migrations disagree.

## Code conventions
- Each API module has `router.py` (HTTP layer) plus `service.py` / `schemas.py` where the logic is non-trivial. Routers do not contain business logic.
- All ML features are computed by `app/ml/features.py`, the single code path for training and inference.
- Errors are raised as `AppError` subclasses (`app/core/errors.py`) and rendered as RFC 9457 problem details.
- Lint/format: `ruff check`, `ruff format`. Types: `mypy app`. Frontend: `npm run lint`, `npm run typecheck`.
