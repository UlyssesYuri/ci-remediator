from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import session_scope
from app.models import STATUSES, Attempt, Run


def persist_run(run: dict) -> dict:
    full_name = run.get("full_name")
    github_run_id = run.get("github_run_id")
    if not full_name or github_run_id is None:
        raise ValueError("full_name and github_run_id are required")

    with session_scope() as session:
        existing = session.scalar(
            select(Run).where(
                Run.full_name == full_name,
                Run.github_run_id == int(github_run_id),
            )
        )
        if existing is not None:
            return _public_row(existing, created=False)

        try:
            with session.begin_nested():
                row = _insert_run(session, run)
        except IntegrityError:
            existing = session.scalar(
                select(Run).where(
                    Run.full_name == full_name,
                    Run.github_run_id == int(github_run_id),
                )
            )
            if existing is None:
                raise
            return _public_row(existing, created=False)

        return _public_row(row, created=True)


def _insert_run(session, run: dict) -> Run:
    row = Run(
        id=str(uuid4()),
        full_name=run["full_name"],
        github_run_id=int(run["github_run_id"]),
        head_sha=run.get("head_sha"),
        head_branch=run.get("head_branch"),
        clone_url=run.get("clone_url"),
        html_url=run.get("html_url"),
        workflow_path=run.get("workflow_path"),
        conclusion=run.get("conclusion"),
        logs_url=run.get("logs_url"),
        status="queued",
        log_s3_key=None,
    )
    session.add(row)
    session.flush()
    return row


def _public_row(run: Run, *, created: bool) -> dict:
    return {
        "run_id": run.id,
        "full_name": run.full_name,
        "clone_url": run.clone_url,
        "github_run_id": run.github_run_id,
        "head_sha": run.head_sha,
        "head_branch": run.head_branch,
        "html_url": run.html_url,
        "workflow_path": run.workflow_path,
        "conclusion": run.conclusion,
        "logs_url": run.logs_url,
        "status": run.status,
        "log_s3_key": run.log_s3_key,
        "sqs_message_id": run.sqs_message_id,
        "created": created,
    }


def get_run(run_id: str) -> dict:
    with session_scope() as session:
        row = session.get(Run, run_id)
        if row is None:
            raise LookupError(run_id)
        return {
            "run_id": row.id,
            "status": row.status,
            "log_s3_key": row.log_s3_key,
            "pr_url": row.pr_url,
        }


def mark_enqueued(run_id: str, message_id: str) -> None:
    with session_scope() as session:
        row = session.get(Run, run_id)
        if row is None:
            raise LookupError(run_id)
        row.sqs_message_id = message_id


def set_status(run_id: str, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    with session_scope() as session:
        row = session.get(Run, run_id)
        if row is None:
            raise LookupError(run_id)
        row.status = status


def set_log_s3_key(run_id: str, log_s3_key: str) -> None:
    with session_scope() as session:
        row = session.get(Run, run_id)
        if row is None:
            raise LookupError(run_id)
        row.log_s3_key = log_s3_key


def set_pr_url(run_id: str, pr_url: str) -> None:
    # Opening the PR ends the run: it is always ready for review, never a draft.
    with session_scope() as session:
        row = session.get(Run, run_id)
        if row is None:
            raise LookupError(run_id)
        row.pr_url = pr_url
        row.status = "succeeded"


def add_attempt(run_id: str, n: int, test_output: str | None, passed: bool | None) -> None:
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise LookupError(run_id)
        existing = session.scalar(
            select(Attempt).where(Attempt.run_id == run_id, Attempt.n == n)
        )
        if existing is not None:
            existing.test_output = test_output
            existing.passed = passed
        else:
            session.add(
                Attempt(run_id=run_id, n=n, test_output=test_output, passed=passed)
            )
