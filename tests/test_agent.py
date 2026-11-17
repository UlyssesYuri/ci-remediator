import zipfile
from io import BytesIO
from pathlib import Path

import httpx
import pytest

import app.agent.nodes as nodes
from app.agent.commands import plan_for
from app.agent.git import authed_clone_url, git
from app.agent.graph import graph, initial_state
from app.agent.logs import log_text_from_zip
from app.agent.sandbox_env import sandbox_env
from app.agent.state import MAX_ATTEMPTS
from app.runs import get_run, persist_run


def test_yaml_command_wins_over_claude_validate_line() -> None:
    setup, gate = plan_for("steps:\n  - run: pytest -q\n", "VALIDATE: npm test")
    assert gate == ["pytest", "-q"]


def test_claude_names_command_when_yaml_has_none() -> None:
    setup, gate = plan_for(
        "steps:\n  - run: echo hello\n", "VALIDATE: python -m pytest tests"
    )
    assert gate == ["python", "-m", "pytest", "tests"]


def test_setup_steps_before_gate_are_kept() -> None:
    workflow = "steps:\n  - run: make build\n  - run: make test\n  - run: make install\n"
    setup, gate = plan_for(workflow, "")
    assert setup == [["make", "build"]]
    assert gate == ["make", "test"]


def test_claude_cannot_name_a_setup_step() -> None:
    setup, gate = plan_for("steps:\n  - run: echo hi\n", "VALIDATE: pip install evil")
    assert gate is None


def test_setup_failure_stops_before_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_gate(argv, cwd):
        if argv == ["make", "build"]:
            return "boom", False
        return "should not run", True

    monkeypatch.setattr(nodes, "run_gate", fake_run_gate)
    output, passed = nodes._run_validation([["make", "build"]], ["make", "test"], ".")
    assert passed is False
    assert output.startswith("setup failed: make build")


def test_unsafe_commands_give_no_gate() -> None:
    setup, gate = plan_for(
        "steps:\n  - run: bash -lc 'rm -rf /'\n", "VALIDATE: curl http://example.com"
    )
    assert gate is None


def test_log_text_from_zip_keeps_the_last_24000_characters() -> None:
    long_text = "x" * 30_000
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("job.txt", long_text)
    text = log_text_from_zip(buf.getvalue())
    assert len(text) == 24_000
    assert text == long_text[-24_000:]


def test_authed_clone_url_does_not_change_the_stored_url() -> None:
    clone_url = "https://github.com/acme/app.git"
    authed = authed_clone_url(clone_url, "secret-token")
    assert clone_url == "https://github.com/acme/app.git"
    assert authed == "https://x-access-token:secret-token@github.com/acme/app.git"


def test_create_pr_sends_no_draft_field_and_base_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent.pr as pr_module

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(201, json={"html_url": "https://github.com/acme/app/pull/1"}, request=request)

    monkeypatch.setattr(pr_module.httpx, "post", fake_post)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    url = pr_module._create_pr(
        {"full_name": "acme/app", "github_run_id": 1, "head_branch": None, "diagnosis": "fix"},
        "ci-remediate/1",
    )
    assert url == "https://github.com/acme/app/pull/1"
    assert "draft" not in captured["json"]
    assert captured["json"]["base"] == "main"


def test_git_failure_keeps_githubs_reason_and_hides_the_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    monkeypatch.setenv("GITHUB_TOKEN", "super-secret-token")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            returncode=1,
            stdout="",
            stderr="remote: Permission denied for token super-secret-token",
        )

    import app.agent.git as git_module

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        git(tmp_path, "push", "https://x-access-token:super-secret-token@github.com/acme/app.git")

    message = str(excinfo.value)
    assert "super-secret-token" not in message
    assert "***" in message
    assert "Permission denied" in message


def test_sandbox_env_hides_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")

    env = sandbox_env()
    assert "GITHUB_TOKEN" not in env
    assert "DATABASE_URL" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env

    env_with_extra = sandbox_env("ANTHROPIC_API_KEY")
    assert env_with_extra["ANTHROPIC_API_KEY"] == "anthropic-secret"
    assert "GITHUB_TOKEN" not in env_with_extra


def _fake_clone(workflow_text: str):
    def clone(clone_url: str, head_sha: str, repo: Path) -> None:
        repo.mkdir(parents=True, exist_ok=True)
        workflow = repo / ".github" / "workflows"
        workflow.mkdir(parents=True, exist_ok=True)
        (workflow / "ci.yml").write_text(workflow_text)
        (repo / "edited.txt").write_text("fixed")

    return clone


def _message(run_id: str, run_number: int) -> dict:
    return {
        "run_id": run_id,
        "full_name": "acme/app",
        "clone_url": "https://github.com/acme/app.git",
        "github_run_id": run_number,
        "head_sha": "abc1234",
        "head_branch": "main",
        "workflow_path": ".github/workflows/ci.yml",
    }


def test_full_graph_fail_then_pass_opens_one_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = persist_run(
        {
            "full_name": "acme/app",
            "github_run_id": 9001,
            "clone_url": "https://github.com/acme/app.git",
            "head_sha": "abc1234",
            "head_branch": "main",
            "html_url": "https://github.com/acme/app/actions/runs/9001",
            "workflow_path": ".github/workflows/ci.yml",
            "conclusion": "failure",
            "logs_url": "https://api.github.com/repos/acme/app/actions/runs/9001/logs",
        }
    )
    run_id = row["run_id"]

    monkeypatch.setattr(nodes, "clone_at_sha", _fake_clone("steps:\n  - run: pytest -q\n"))
    monkeypatch.setattr(nodes, "read_archived_logs", lambda key: "some log text")

    prompts = []
    claude_calls = {"n": 0}

    def fake_run_claude(prompt, cwd):
        prompts.append(prompt)
        claude_calls["n"] += 1
        return f"diagnosis {claude_calls['n']}"

    monkeypatch.setattr(nodes, "run_claude", fake_run_claude)

    gate_calls = {"n": 0}

    def fake_run_gate(argv, cwd):
        gate_calls["n"] += 1
        if gate_calls["n"] == 1:
            return "AssertionError: boom", False
        return "ok", True

    monkeypatch.setattr(nodes, "run_gate", fake_run_gate)

    from app.runs import set_pr_url

    def fake_open_pr(state):
        set_pr_url(state["run_id"], "https://github.com/acme/app/pull/5")
        return "https://github.com/acme/app/pull/5"

    monkeypatch.setattr(nodes, "open_pr", fake_open_pr)

    graph.invoke(initial_state(_message(run_id, 9001), "runs/acme/app/9001/logs.zip"))

    assert claude_calls["n"] == 2
    assert "Previous test output" in prompts[1]

    final = get_run(run_id)
    assert final["status"] == "succeeded"
    assert final["pr_url"] == "https://github.com/acme/app/pull/5"

    assert not nodes.run_dir(run_id).exists()


def test_full_graph_three_fails_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    row = persist_run(
        {
            "full_name": "acme/app",
            "github_run_id": 9002,
            "clone_url": "https://github.com/acme/app.git",
            "head_sha": "abc1234",
            "head_branch": "main",
            "html_url": "https://github.com/acme/app/actions/runs/9002",
            "workflow_path": ".github/workflows/ci.yml",
            "conclusion": "failure",
            "logs_url": "https://api.github.com/repos/acme/app/actions/runs/9002/logs",
        }
    )
    run_id = row["run_id"]

    monkeypatch.setattr(nodes, "clone_at_sha", _fake_clone("steps:\n  - run: pytest -q\n"))
    monkeypatch.setattr(nodes, "read_archived_logs", lambda key: "some log text")

    claude_calls = {"n": 0}

    def fake_run_claude(prompt, cwd):
        claude_calls["n"] += 1
        return f"diagnosis {claude_calls['n']}"

    monkeypatch.setattr(nodes, "run_claude", fake_run_claude)
    monkeypatch.setattr(nodes, "run_gate", lambda argv, cwd: ("still failing", False))

    def fail_open_pr(state):
        raise AssertionError("should not be called")

    monkeypatch.setattr(nodes, "open_pr", fail_open_pr)

    graph.invoke(initial_state(_message(run_id, 9002), "runs/acme/app/9002/logs.zip"))

    assert claude_calls["n"] == MAX_ATTEMPTS
    final = get_run(run_id)
    assert final["status"] == "needs_manual"
    assert final["pr_url"] is None
