import io
import json
import zipfile

import httpx
import pytest

import worker.sqs_worker as worker_module
from app.runs import get_run, persist_run, set_log_s3_key
from app.s3 import bucket_name, client as s3_client, logs_key
from app.sqs import client as sqs_client, queue_url
from worker import logs as worker_logs


@pytest.fixture(autouse=True)
def _worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_S3_BUCKET", "ci-remediator-logs-test")
    monkeypatch.setenv("GITHUB_TOKEN", "super-secret-token")
    s3_client().create_bucket(Bucket=bucket_name())
    # The queue is shared with every other test module in this session;
    # drain whatever other tests left on it so poll_once() sees only the
    # message this test sends.
    while True:
        response = sqs_client().receive_message(QueueUrl=queue_url(), MaxNumberOfMessages=10)
        messages = response.get("Messages") or []
        if not messages:
            break
        for message in messages:
            sqs_client().delete_message(QueueUrl=queue_url(), ReceiptHandle=message["ReceiptHandle"])


class _FakeGraph:
    def __init__(self):
        self.calls = []

    def invoke(self, state):
        self.calls.append(state)


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("job.txt", "all good")
    return buf.getvalue()


def _row(run_number: int) -> dict:
    return persist_run(
        {
            "full_name": "acme/app",
            "github_run_id": run_number,
            "clone_url": "https://github.com/acme/app.git",
            "head_sha": "abc1234",
            "head_branch": "main",
            "html_url": f"https://github.com/acme/app/actions/runs/{run_number}",
            "workflow_path": ".github/workflows/ci.yml",
            "conclusion": "failure",
            "logs_url": f"https://api.github.com/repos/acme/app/actions/runs/{run_number}/logs",
        }
    )


def _message(row: dict, run_number: int) -> dict:
    return {
        "run_id": row["run_id"],
        "full_name": "acme/app",
        "clone_url": "https://github.com/acme/app.git",
        "github_run_id": run_number,
        "head_sha": "abc1234",
        "head_branch": "main",
        "html_url": f"https://github.com/acme/app/actions/runs/{run_number}",
        "workflow_path": ".github/workflows/ci.yml",
        "conclusion": "failure",
        "logs_url": f"https://api.github.com/repos/acme/app/actions/runs/{run_number}/logs",
    }


def test_worker_archives_logs_before_the_graph(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    row = _row(9101)
    message = _message(row, 9101)

    def fake_get(url, headers=None, follow_redirects=None, timeout=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, content=_zip_bytes(), request=request)

    monkeypatch.setattr(httpx, "get", fake_get)

    fake_graph = _FakeGraph()
    monkeypatch.setattr(worker_module, "graph", fake_graph)

    url = sqs_client().send_message(QueueUrl=queue_url(), MessageBody=json.dumps(message))

    worker_module.poll_once()

    assert len(fake_graph.calls) == 1
    assert "logs_url" not in fake_graph.calls[0]

    key = logs_key("acme/app", 9101)
    obj = s3_client().get_object(Bucket=bucket_name(), Key=key)
    assert obj["Body"].read() == _zip_bytes()

    final = get_run(row["run_id"])
    assert final["log_s3_key"] == key

    attrs = sqs_client().get_queue_attributes(
        QueueUrl=queue_url(),
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )
    assert attrs["Attributes"]["ApproximateNumberOfMessages"] == "0"
    assert attrs["Attributes"]["ApproximateNumberOfMessagesNotVisible"] == "0"

    out = capsys.readouterr().out.strip().splitlines()
    assert out == [
        f"run 9101: received acme/app",
        f"run 9101: logs archived to S3 at {key}",
        "run 9101: done, message deleted",
    ]
    assert "super-secret-token" not in "\n".join(out)


def test_worker_reuses_existing_log_s3_key(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _row(9102)
    key = logs_key("acme/app", 9102)
    s3_client().put_object(Bucket=bucket_name(), Key=key, Body=_zip_bytes())
    set_log_s3_key(row["run_id"], key)

    def boom(*args, **kwargs):
        raise AssertionError("fetch_logs must not be called")

    monkeypatch.setattr(worker_logs, "fetch_logs", boom)

    fake_graph = _FakeGraph()
    monkeypatch.setattr(worker_module, "graph", fake_graph)

    message = _message(row, 9102)
    worker_module.process_message(message)

    assert len(fake_graph.calls) == 1


def test_graph_invoke_raising_leaves_the_message_on_the_queue(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    row = _row(9103)
    message = _message(row, 9103)

    def fake_get(url, headers=None, follow_redirects=None, timeout=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, content=_zip_bytes(), request=request)

    monkeypatch.setattr(httpx, "get", fake_get)

    class _RaisingGraph:
        def invoke(self, state):
            raise RuntimeError("git push failed: permission denied")

    monkeypatch.setattr(worker_module, "graph", _RaisingGraph())

    sqs_client().send_message(QueueUrl=queue_url(), MessageBody=json.dumps(message))

    worker_module.poll_once()

    out = capsys.readouterr().out
    assert "WARNING: run 9103: worker left the message on the queue" in out
    assert "done, message deleted" not in out

    attrs = sqs_client().get_queue_attributes(
        QueueUrl=queue_url(),
        AttributeNames=["ApproximateNumberOfMessagesNotVisible"],
    )
    assert attrs["Attributes"]["ApproximateNumberOfMessagesNotVisible"] == "1"
