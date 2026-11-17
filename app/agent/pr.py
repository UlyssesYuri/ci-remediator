from pathlib import Path

import httpx

from app.agent.git import git, push_branch
from app.agent.github import github_token
from app.runs import set_pr_url, set_status


def open_pr(state: dict) -> str:
    repo = Path(state["sandbox_path"])
    status = git(repo, "status", "--porcelain")
    if not status.stdout.strip():
        # A passing gate with no edit is not a pull request.
        set_status(state["run_id"], "needs_manual")
        return ""

    branch = f"ci-remediate/{state['github_run_id']}"
    git(repo, "config", "user.email", "ci-remediator@local")
    git(repo, "config", "user.name", "ci-remediator")
    git(repo, "checkout", "-b", branch)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", f"Remediate CI run {state['github_run_id']}")
    push_branch(repo, state["clone_url"], branch)
    url = _create_pr(state, branch)
    set_pr_url(state["run_id"], url)
    return url


def _create_pr(state: dict, branch: str) -> str:
    response = httpx.post(
        f"https://api.github.com/repos/{state['full_name']}/pulls",
        headers={
            "Authorization": f"Bearer {github_token()}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ci-remediator",
        },
        json={
            "title": f"Remediate CI run {state['github_run_id']}",
            "head": branch,
            "base": state.get("head_branch") or "main",
            "body": (state.get("diagnosis") or "")[:6000],
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["html_url"]
