import shutil
import subprocess
from pathlib import Path

from app.agent.claude import run_claude
from app.agent.commands import PREFIXES, plan_for
from app.agent.git import clone_at_sha
from app.agent.logs import read_archived_logs
from app.agent.pr import open_pr
from app.agent.sandbox_env import sandbox_env
from app.agent.state import MAX_ATTEMPTS, AgentState
from app.runs import add_attempt, set_status

GATE_TIMEOUT = 600


def run_dir(run_id: str) -> Path:
    return Path("/tmp") / "ci-remediator" / run_id


def log(github_run_id, message: str) -> None:
    print(f"run {github_run_id}: {message}", flush=True)


def claim(state: AgentState) -> dict:
    set_status(state["run_id"], "processing")
    return {}


def prepare(state: AgentState) -> dict:
    root = run_dir(state["run_id"])
    if root.exists():
        shutil.rmtree(root)
    repo = root / "repo"
    clone_at_sha(state["clone_url"], state["head_sha"], repo)
    # The zip is already in S3. Prepare does not call logs_url.
    log_text = read_archived_logs(state["log_s3_key"])
    log(state["github_run_id"], f"cloned {state['full_name']} at {state['head_sha'][:7]}")
    return {"sandbox_path": str(repo), "log_text": log_text}


def remediate(state: AgentState) -> dict:
    previous = state.get("test_output") or ""
    previous_block = f"\nPrevious test output:\n{previous}\n" if previous else ""
    allowlist = ", ".join(PREFIXES)
    prompt = (
        "You are repairing a failed CI run inside this repository.\n"
        "Read the logs, find the cause, and edit the code.\n"
        "Do not run tests. Do not commit. Do not push. "
        "Do not open a pull request.\n"
        "If the workflow YAML does not already name an allowlisted test "
        "command, end your reply with one line: VALIDATE: <command>. "
        f"The command must start with one of: {allowlist}.\n"
        f"Attempt {state['n']} of {MAX_ATTEMPTS}.\n"
        f"Workflow: {state.get('workflow_path') or ''}\n"
        f"{previous_block}\n"
        "Logs:\n"
        f"{state.get('log_text') or ''}\n"
    )
    log(state["github_run_id"], f"remediate attempt {state['n']} of {MAX_ATTEMPTS}: claude -p")
    diagnosis = run_claude(prompt, state["sandbox_path"])
    return {"diagnosis": diagnosis}


def run_gate(argv: list[str], cwd: str) -> tuple[str, bool]:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GATE_TIMEOUT,
            shell=False,
            env=sandbox_env(),
        )
    except subprocess.TimeoutExpired:
        return f"gate timed out after {GATE_TIMEOUT}s", False
    except FileNotFoundError:
        return f"{argv[0]} is not installed on this worker", False
    output = (completed.stdout or "") + (completed.stderr or "")
    return output, completed.returncode == 0


def _run_validation(setup: list[list[str]], gate: list[str] | None, cwd: str) -> tuple[str, bool]:
    if gate is None:
        return "no allowlisted test command", False
    for step in setup:
        output, ok = run_gate(step, cwd)
        if not ok:
            return f"setup failed: {' '.join(step)}\n{output}", False
    return run_gate(gate, cwd)


def _workflow_text(state: AgentState) -> str:
    workflow_path = state.get("workflow_path")
    if not workflow_path:
        return ""
    path = Path(state["sandbox_path"]) / workflow_path
    if not path.exists():
        return ""
    return path.read_text(errors="replace")


def validate(state: AgentState) -> dict:
    setup, gate = plan_for(_workflow_text(state), state.get("diagnosis") or "")
    output, passed = _run_validation(setup, gate, state["sandbox_path"])
    add_attempt(state["run_id"], state["n"], output, passed)
    commands = ", ".join(" ".join(step) for step in setup + ([gate] if gate else []))
    log(
        state["github_run_id"],
        f"validate attempt {state['n']}: {commands or 'none'} -> {'passed' if passed else 'failed'}",
    )
    return {"test_output": output, "passed": passed}


def decide(state: AgentState) -> dict:
    if state.get("passed"):
        return {"route": "succeed"}
    if state["n"] >= MAX_ATTEMPTS:
        return {"route": "escalate"}
    return {"route": "retry", "n": state["n"] + 1}


def route_after_decide(state: AgentState) -> str:
    return state["route"]


def succeed(state: AgentState) -> dict:
    url = open_pr(state)
    if url:
        log(state["github_run_id"], f"PR opened {url}")
    else:
        log(state["github_run_id"], "gate passed with no change, needs_manual, no PR")
    return {}


def escalate(state: AgentState) -> dict:
    set_status(state["run_id"], "needs_manual")
    log(state["github_run_id"], f"{MAX_ATTEMPTS} attempts failed, needs_manual, no PR")
    return {}


def cleanup(state: AgentState) -> dict:
    shutil.rmtree(run_dir(state["run_id"]), ignore_errors=True)
    return {"sandbox_path": ""}
