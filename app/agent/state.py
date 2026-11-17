from typing import TypedDict

MAX_ATTEMPTS = 3  # then needs_manual
LOG_TAIL = 24_000  # characters of log Claude sees


class AgentState(TypedDict, total=False):
    run_id: str
    full_name: str
    clone_url: str
    github_run_id: int
    head_sha: str
    head_branch: str | None
    workflow_path: str | None
    log_s3_key: str
    sandbox_path: str
    log_text: str
    n: int
    diagnosis: str
    test_output: str
    passed: bool
    route: str
