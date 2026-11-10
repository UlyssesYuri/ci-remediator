import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.db import database_url, init_db
from app.runs import mark_enqueued, persist_run
from app.sqs import queue_url, send_message

_TERMINAL_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if database_url() is not None:
        init_db()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request) -> dict[str, bool]:
    raw = await request.body()
    if not raw or not raw.strip():
        return {"accepted": True}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"accepted": True}
    if not isinstance(payload, dict):
        return {"accepted": True}

    run = extract_run(payload)
    if run is None:
        return {"accepted": True}

    try:
        row = persist_run(run)
    except Exception as exc:
        print(
            f"WARNING: persist failed; event accepted and not stored: {exc}",
            flush=True,
        )
        return {"accepted": True}

    if row.get("sqs_message_id"):
        return {"accepted": True}

    try:
        message_id = enqueue_run(row)
        if message_id:
            mark_enqueued(row["run_id"], message_id)
    except Exception as exc:
        print(
            f"WARNING: SendMessage failed; event accepted and not queued: {exc}",
            flush=True,
        )
    return {"accepted": True}


def extract_run(payload: dict) -> dict | None:
    if payload.get("action") != "completed":
        return None

    workflow_run = payload.get("workflow_run")
    if not isinstance(workflow_run, dict):
        return None

    conclusion = workflow_run.get("conclusion")
    if conclusion not in _TERMINAL_CONCLUSIONS:
        return None

    repository = payload.get("repository")
    if not isinstance(repository, dict):
        repository = {}

    return {
        "full_name": repository.get("full_name"),
        "clone_url": repository.get("clone_url"),
        "github_run_id": workflow_run.get("id"),
        "head_sha": workflow_run.get("head_sha"),
        "head_branch": workflow_run.get("head_branch"),
        "html_url": workflow_run.get("html_url"),
        "workflow_path": workflow_run.get("path"),
        "conclusion": conclusion,
        "logs_url": workflow_run.get("logs_url"),
    }


def queue_body(row: dict) -> dict:
    """What the worker needs. status and sqs_message_id stay on the row."""
    return {
        "run_id": row["run_id"],
        "full_name": row["full_name"],
        "clone_url": row["clone_url"],
        "github_run_id": row["github_run_id"],
        "head_sha": row["head_sha"],
        "head_branch": row["head_branch"],
        "html_url": row["html_url"],
        "workflow_path": row["workflow_path"],
        "conclusion": row["conclusion"],
        "logs_url": row["logs_url"],
    }


def enqueue_run(row: dict) -> str | None:
    body = json.dumps(queue_body(row))
    if not queue_url():
        print(
            "WARNING: SQS_QUEUE_URL is not set; message was not sent",
            flush=True,
        )
        print(body, flush=True)
        return None
    message_id = send_message(body)
    print(body, flush=True)
    return message_id
