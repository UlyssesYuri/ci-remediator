import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


def _redact(text: str) -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    return text.replace(token, "***") if token else text


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {_redact(completed.stderr)}")
    return completed


def authed_clone_url(clone_url: str, token: str) -> str:
    parts = urlsplit(clone_url)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError(f"not an https clone_url: {clone_url}")
    rest = clone_url[len(f"{parts.scheme}://"):]
    host_and_path = rest.split("@", 1)[-1]
    return f"https://x-access-token:{token}@{host_and_path}"


def clone_at_sha(clone_url: str, head_sha: str, repo: Path) -> None:
    repo.parent.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    authed = authed_clone_url(clone_url, token)
    git(repo.parent, "clone", "--quiet", authed, str(repo))
    # Claude reads this checkout. Do not leave the token in .git/config.
    git(repo, "remote", "set-url", "origin", clone_url)
    git(repo, "fetch", "--quiet", authed, head_sha)
    git(repo, "checkout", "--quiet", head_sha)


def push_branch(repo: Path, clone_url: str, branch: str) -> None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    authed = authed_clone_url(clone_url, token)
    git(repo, "push", "--quiet", authed, f"HEAD:refs/heads/{branch}")
