import json
import socket
import sys
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _payload(action: str, conclusion: str | None) -> dict:
    return {
        "action": action,
        "repository": {
            "full_name": "acme/app",
            "clone_url": "https://github.com/acme/app.git",
        },
        "workflow_run": {
            "id": 123,
            "head_sha": "abc123",
            "head_branch": "main",
            "conclusion": conclusion,
            "html_url": "https://github.com/acme/app/actions/runs/123",
            "path": ".github/workflows/ci.yml",
            "logs_url": "https://api.github.com/repos/acme/app/actions/runs/123/logs",
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
    assert message["github_run_id"] == 123
    assert message["head_sha"] == "abc123"
    assert message["head_branch"] == "main"
    assert message["html_url"].endswith("/123")
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
