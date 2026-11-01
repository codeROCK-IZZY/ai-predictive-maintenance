"""Command-line tools: ``python -m app.cli <command>``.

Commands
  migrate                        apply database migrations
  create-admin --email E         create an admin user (password from PDM_ADMIN_PASSWORD or prompt)
  seed-catalog                   insert default equipment types and sensors
  load-demo [--units --days --seed]   generate and load a synthetic fleet
  export-sample-csv --out F      write a small CSV in the upload format (for trying the CSV import)
  train [--algorithm --no-compare]    train, evaluate, register and activate a model
  evaluate --out DIR             offline evaluation on an in-memory synthetic fleet (writes a report)
  score-all                      score every active unit with the active model
  backfill [--days]              replay the active model over recent history (risk trend + alerts)
  worker                         run the background job worker
  demo                           migrate + catalog + demo data + train + backfill (one command setup)
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "migrations"))
    cfg.attributes["database_url"] = get_settings().database_url
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")
    print("migrations applied")


def _create_admin(email: str, full_name: str) -> None:
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    password = os.environ.get("PDM_ADMIN_PASSWORD") or getpass.getpass("Admin password (min 12 chars): ")
    if len(password) < 12:
        sys.exit("password must be at least 12 characters")
    with session_scope() as db:
        if db.scalar(select(User).where(User.email == email.lower())):
            print(f"user {email} already exists — skipped")
            return
        db.add(User(email=email.lower(), full_name=full_name, password_hash=hash_password(password), role="admin"))
    print(f"admin {email} created")


def _seed_catalog() -> None:
    from app.db.session import session_scope
    from app.modules.equipment.service import seed_catalog

    with session_scope() as db:
        seed_catalog(db)
    print("catalog seeded")


def _load_demo(units: int, days: int, seed: int) -> None:
    from app.db.session import session_scope
    from app.ml.demo import load_demo
    from app.ml.synthetic import FleetConfig

    with session_scope() as db:
        stats = load_demo(db, FleetConfig(n_units=units, days=days, seed=seed))
    print(json.dumps(stats))


def _export_sample_csv(out: Path) -> None:
    from app.ml.synthetic import FleetConfig, generate_fleet

    fleet = generate_fleet(FleetConfig(n_units=3, days=2, seed=7))
    r = fleet.readings.rename(columns={"serial": "equipment_serial"})
    r["ts"] = r["ts"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.parent.mkdir(parents=True, exist_ok=True)
    r[["equipment_serial", "sensor", "ts", "value"]].to_csv(out, index=False)
    print(f"wrote {len(r)} rows to {out} (serials: {sorted(r.equipment_serial.unique())})")


def _train(algorithm: str, compare: bool) -> None:
    from app.db.session import session_scope
    from app.ml.pipeline import train_from_db

    with session_scope() as db:
        mv = train_from_db(db, algorithm=algorithm, compare=compare, activate=True)
        print(
            json.dumps(
                {
                    "version": mv.version,
                    "algorithm": mv.algorithm,
                    "holdout": mv.metrics["holdout"]["production_calibrated"]["pr_auc"],
                }
            )
        )


def _score_all() -> None:
    from app.db.session import session_scope
    from app.jobs.handlers import handle_score_all

    with session_scope() as db:
        print(json.dumps(handle_score_all(db, {})))


def _backfill(days: int) -> None:
    from app.db.session import session_scope
    from app.ml.backfill import backfill

    with session_scope() as db:
        print(json.dumps(backfill(db, days=days)))


def _evaluate(out_dir: Path, units: int, days: int, seed: int) -> None:
    from app.ml.dataset import build_training_set, fleet_to_histories
    from app.ml.pipeline import clean_nans
    from app.ml.report import write_markdown
    from app.ml.synthetic import FleetConfig, generate_fleet
    from app.ml.training import evaluate

    cfg = FleetConfig(n_units=units, days=days, seed=seed, end=datetime(2026, 10, 1, tzinfo=UTC))
    ds = build_training_set(fleet_to_histories(generate_fleet(cfg)))
    report = clean_nans(json.loads(json.dumps(evaluate(ds, seed=seed), default=str)))
    report["fleet_config"] = {
        "n_units": units,
        "days": days,
        "seed": seed,
        "freq_minutes": cfg.freq_minutes,
        "end": cfg.end.isoformat(),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "evaluation.json").write_text(json.dumps(report, indent=2))
    write_markdown(report, out_dir / "evaluation.md")
    print(f"wrote {out_dir / 'evaluation.json'} and {out_dir / 'evaluation.md'}")


def main(argv: list[str] | None = None) -> None:
    configure_logging(get_settings().log_level, as_json=False)
    p = argparse.ArgumentParser(
        prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    a = sub.add_parser("create-admin")
    a.add_argument("--email", required=True)
    a.add_argument("--name", default="Administrator")
    sub.add_parser("seed-catalog")
    d = sub.add_parser("load-demo")
    for sp in (d,):
        sp.add_argument("--units", type=int, default=40)
        sp.add_argument("--days", type=int, default=365)
        sp.add_argument("--seed", type=int, default=42)
    e = sub.add_parser("export-sample-csv")
    e.add_argument("--out", type=Path, default=Path("../data/sample/sample_readings.csv"))
    t = sub.add_parser("train")
    t.add_argument("--algorithm", choices=["auto", "logistic_regression", "xgboost"], default="auto")
    t.add_argument("--no-compare", action="store_true")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--out", type=Path, default=Path("../ml/reports"))
    ev.add_argument("--units", type=int, default=40)
    ev.add_argument("--days", type=int, default=365)
    ev.add_argument("--seed", type=int, default=42)
    sub.add_parser("score-all")
    bf = sub.add_parser("backfill")
    bf.add_argument("--days", type=int, default=30)
    sub.add_parser("worker")
    demo = sub.add_parser("demo")
    demo.add_argument("--units", type=int, default=40)
    demo.add_argument("--days", type=int, default=365)
    demo.add_argument("--admin-email", default=os.environ.get("PDM_ADMIN_EMAIL"))
    args = p.parse_args(argv)

    if args.cmd == "migrate":
        _migrate()
    elif args.cmd == "create-admin":
        _create_admin(args.email, args.name)
    elif args.cmd == "seed-catalog":
        _seed_catalog()
    elif args.cmd == "load-demo":
        _load_demo(args.units, args.days, args.seed)
    elif args.cmd == "export-sample-csv":
        _export_sample_csv(args.out)
    elif args.cmd == "train":
        _train(args.algorithm, not args.no_compare)
    elif args.cmd == "evaluate":
        _evaluate(args.out, args.units, args.days, args.seed)
    elif args.cmd == "score-all":
        _score_all()
    elif args.cmd == "backfill":
        _backfill(args.days)
    elif args.cmd == "worker":
        from app.jobs.worker import run_forever

        run_forever()
    elif args.cmd == "demo":
        _migrate()
        _seed_catalog()
        if args.admin_email:
            _create_admin(args.admin_email, "Administrator")
        _load_demo(args.units, args.days, 42)
        _train("auto", True)
        _backfill(30)


if __name__ == "__main__":
    main()
