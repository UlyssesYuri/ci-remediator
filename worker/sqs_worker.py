import json
import shutil
import time

from app.agent.graph import graph, initial_state
from app.agent.nodes import log, run_dir
from app.db import init_db
from app.runs import get_run
from app.sqs import client, queue_url
from worker.logs import archive_logs

# Longer than three Claude calls and three gates at their timeouts.
VISIBILITY_TIMEOUT = 3 * (900 + 600) + 600
RETRY_SECONDS = 20


def process_message(body: dict) -> None:
    run_id = body["run_id"]
    github_run_id = body.get("github_run_id")
    log(github_run_id, f"received {body.get('full_name')}")

    row = get_run(run_id)
    if row["status"] in {"succeeded", "needs_manual"}:
        log(github_run_id, f"already {row['status']}, skipped")
        return

    if row["log_s3_key"]:
        key = row["log_s3_key"]
        log(github_run_id, f"logs already in S3 at {key}")
    else:
        key = archive_logs(body)
        log(github_run_id, f"logs archived to S3 at {key}")

    try:
        graph.invoke(initial_state(body, key))
    finally:
        shutil.rmtree(run_dir(run_id), ignore_errors=True)


def poll_once() -> None:
    url = queue_url()
    if not url:
        raise RuntimeError("SQS_QUEUE_URL is not set")

    response = client().receive_message(
        QueueUrl=url,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=20,
        VisibilityTimeout=VISIBILITY_TIMEOUT,
    )
    for message in response.get("Messages") or []:
        body: dict = {}
        try:
            body = json.loads(message["Body"])
            process_message(body)
        except Exception as exc:
            print(
                f"WARNING: run {body.get('github_run_id')}: "
                f"worker left the message on the queue: {exc}",
                flush=True,
            )
            continue
        client().delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
        log(body.get("github_run_id"), "done, message deleted")


def main() -> None:
    ready = False
    while True:
        try:
            if not ready:
                init_db()
                ready = True
                print("worker ready", flush=True)
            poll_once()
        except Exception as exc:
            print(f"WARNING: worker cannot poll yet: {exc}", flush=True)
            time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    main()
