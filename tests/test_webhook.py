import itertools
import json
import socket
import sys
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app

client = TestClient(app)

_run_id_counter = itertools.count(1000)


def _next_run_id() -> int:
    return next(_run_id_counter)


def _payload(action: str, conclusion: str | None, run_id: int | None = None) -> dict:
    if run_id is None:
        run_id = _next_run_id()
    return {
        "action": action,
        "repository": {
            "full_name": "acme/app",
            "clone_url": "https://github.com/acme/app.git",
        },
        "workflow_run": {
            "id": run_id,
            "head_sha": "abc123",
            "head_branch": "main",
            "conclusion": conclusion,
            "html_url": f"https://github.com/acme/app/actions/runs/{run_id}",
            "path": ".github/workflows/ci.yml",
            "logs_url": f"https://api.github.com/repos/acme/app/actions/runs/{run_id}/logs",
        },
    }


def test_empty_json_object_is_accepted(capsys: pytest.CaptureFixture[str]) -> None:
    response = client.post("/webhook", json={})
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert capsys.readouterr().out == ""


def test_empty_body_is_accepted(capsys: pytest.CaptureFixture[str]) -> None:
    response = client.post(
        "/webhook", content=b"", headers={"content-type": "application/json"}
    )
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert capsys.readouterr().out == ""


def test_invalid_json_is_accepted(capsys: pytest.CaptureFixture[str]) -> None:
    response = client.post(
        "/webhook",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert capsys.readouterr().out == ""


def test_non_object_json_is_accepted(capsys: pytest.CaptureFixture[str]) -> None:
    response = client.post(
        "/webhook", content=b"[]", headers={"content-type": "application/json"}
    )
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert capsys.readouterr().out == ""


def test_completed_failure_is_accepted_and_enqueues(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = client.post("/webhook", json=_payload("completed", "failure"))
    assert response.status_code == 200
    assert response.json() == {"accepted": True}

    out = capsys.readouterr().out.strip()
    lines = out.splitlines()
    assert len(lines) == 1

    message = json.loads(lines[0])
    UUID(message["run_id"])
    assert "status" not in message
    assert message["full_name"] == "acme/app"
    assert message["clone_url"] == "https://github.com/acme/app.git"
    assert message["head_sha"] == "abc123"
    assert message["head_branch"] == "main"
    assert message["html_url"].endswith(str(message["github_run_id"]))
    assert message["workflow_path"] == ".github/workflows/ci.yml"
    assert message["conclusion"] == "failure"
    assert message["logs_url"].endswith("/logs")


def test_completed_timed_out_is_accepted() -> None:
    response = client.post("/webhook", json=_payload("completed", "timed_out"))
    assert response.status_code == 200
    assert response.json() == {"accepted": True}


def test_completed_startup_failure_is_accepted() -> None:
    response = client.post("/webhook", json=_payload("completed", "startup_failure"))
    assert response.status_code == 200
    assert response.json() == {"accepted": True}


def test_completed_success_prints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    response = client.post("/webhook", json=_payload("completed", "success"))
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert capsys.readouterr().out == ""


def test_push_event_prints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    response = client.post("/webhook", json={"action": "push"})
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert capsys.readouterr().out == ""


def test_repeat_delivery_sends_once(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _payload("completed", "failure")

    first = client.post("/webhook", json=payload)
    second = client.post("/webhook", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200

    out = capsys.readouterr().out.strip()
    lines = [line for line in out.splitlines() if line]
    assert len(lines) == 1


def test_missing_queue_url_warns_and_prints_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SQS_QUEUE_URL", raising=False)

    response = client.post("/webhook", json=_payload("completed", "failure"))
    assert response.status_code == 200

    out = capsys.readouterr().out.strip()
    lines = out.splitlines()
    assert lines[0] == "WARNING: SQS_QUEUE_URL is not set; message was not sent"
    body = json.loads(lines[-1])
    assert "status" not in body


def test_persist_failure_warns_and_does_not_enqueue(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _raise(_run: dict) -> dict:
        raise RuntimeError("rds down")

    monkeypatch.setattr(main_module, "persist_run", _raise)

    response = client.post("/webhook", json=_payload("completed", "failure"))
    assert response.status_code == 200

    out = capsys.readouterr().out
    assert "WARNING: persist failed" in out
    assert not any(line.startswith("{") for line in out.splitlines())


def test_enqueue_failure_is_retried_on_redelivery(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_enqueue_run = main_module.enqueue_run
    calls = {"n": 0}

    def _flaky(row: dict):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("send failed")
        return real_enqueue_run(row)

    monkeypatch.setattr(main_module, "enqueue_run", _flaky)

    payload = _payload("completed", "failure")
    first = client.post("/webhook", json=payload)
    second = client.post("/webhook", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200

    out = capsys.readouterr().out
    assert "WARNING: SendMessage failed" in out
    assert sum(1 for line in out.splitlines() if line.startswith("{")) == 1


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows ProactorEventLoop opens its own socketpair per request; "
    "the app runs on Linux in Docker/CI where this does not apply.",
)
def test_webhook_does_not_open_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("handler must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)

    response = client.post("/webhook", json=_payload("completed", "failure"))
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
