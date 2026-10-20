"""Run integration tests against a separate, Flyway-managed PostgreSQL database."""

import os
from pathlib import Path
import subprocess
import sys

import psycopg

ROOT = Path(__file__).resolve().parents[1]
with psycopg.connect("postgresql://marketplace:marketplace@localhost:5433/postgres", autocommit=True) as db:
    if not db.execute("SELECT 1 FROM pg_database WHERE datname='marketplace_test'").fetchone():
        db.execute("CREATE DATABASE marketplace_test")
subprocess.run(
    [
        "docker",
        "compose",
        "run",
        "--rm",
        "-e",
        "FLYWAY_URL=jdbc:postgresql://db:5432/marketplace_test",
        "migrate",
    ],
    cwd=ROOT,
    check=True,
)
env = dict(
    os.environ,
    DATABASE_URL="postgresql://marketplace:marketplace@localhost:5433/marketplace_test",
    ORDER_INTERVAL_MINUTES="1",
)
raise SystemExit(
    subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=ROOT, env=env).returncode
)
