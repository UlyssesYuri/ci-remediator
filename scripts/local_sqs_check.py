"""Not run by pytest. Posts the example failure twice and shows the queued message.

Usage (with elasticmq running and SQS_ENDPOINT_URL set):
    export DATABASE_URL=sqlite:///./ci-remediator.db
    export SQS_ENDPOINT_URL=http://127.0.0.1:9324
    export SQS_QUEUE_URL=http://127.0.0.1:9324/000000000000/ci-remediator
    export AWS_ACCESS_KEY_ID=test
    export AWS_SECRET_ACCESS_KEY=test
    python scripts/local_sqs_check.py
"""

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app
from app.sqs import client as sqs_client
from app.sqs import queue_url

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "workflow_run.completed.failure.json"


def main() -> None:
    endpoint = os.environ.get("SQS_ENDPOINT_URL", "").strip()
    url = queue_url()
    if endpoint and url:
        name = url.rstrip("/").rsplit("/", 1)[-1]
        sqs_client().create_queue(QueueName=name)

    init_db()
    test_client = TestClient(app)
    payload = json.loads(EXAMPLE.read_text())

    first = test_client.post("/webhook", json=payload)
    second = test_client.post("/webhook", json=payload)
    print("first status:", first.status_code, first.json())
    print("second status:", second.status_code, second.json())

    response = sqs_client().receive_message(
        QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=1
    )
    messages = response.get("Messages") or []
    print("message_count:", len(messages))
    for message in messages:
        print(message["Body"])


if __name__ == "__main__":
    main()
