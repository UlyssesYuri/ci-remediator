"""Not run by pytest. Posts the example failure twice and prints the stored rows.

Usage:
    export DATABASE_URL=postgresql://ci:ci@127.0.0.1:5432/ci_remediator
    python scripts/local_postgres_check.py
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import init_db, session_scope
from app.main import app
from app.models import Run

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "workflow_run.completed.failure.json"


def main() -> None:
    init_db()
    client = TestClient(app)
    payload = json.loads(EXAMPLE.read_text())

    first = client.post("/webhook", json=payload)
    second = client.post("/webhook", json=payload)
    print("first status:", first.status_code)
    print("second status:", second.status_code)

    with session_scope() as session:
        rows = session.scalars(select(Run)).all()
        print("row count:", len(rows))
        for row in rows:
            print(
                {
                    "full_name": row.full_name,
                    "github_run_id": row.github_run_id,
                    "clone_url": row.clone_url,
                    "logs_url": row.logs_url,
                    "conclusion": row.conclusion,
                    "status": row.status,
                    "log_s3_key": row.log_s3_key,
                }
            )


if __name__ == "__main__":
    main()
