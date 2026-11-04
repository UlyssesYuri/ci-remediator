import json
from uuid import uuid4

from fastapi import FastAPI, Request

app = FastAPI()

_TERMINAL_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}


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

    row = persist_run(run)
    enqueue_run(row)
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


def persist_run(run: dict) -> dict:
    """Placeholder until real Postgres exists. Builds the row only."""
    return {
        "run_id": str(uuid4()),
        "status": "queued",
        "log_s3_key": None,
        **run,
    }


def enqueue_run(row: dict) -> None:
    """Placeholder until real SQS exists. Prints the message only."""
    message = {
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
    print(json.dumps(message), flush=True)
