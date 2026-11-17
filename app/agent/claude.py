import subprocess

from app.agent.sandbox_env import sandbox_env

CLAUDE_TIMEOUT = 900


def run_claude(prompt: str, cwd: str) -> str:
    # The prompt is stdin so the log text is not on the process command line.
    completed = subprocess.run(
        ["claude", "-p", "--output-format", "text", "--dangerously-skip-permissions"],
        cwd=cwd,
        input=prompt,
        capture_output=True,
        text=True,
        env=sandbox_env("ANTHROPIC_API_KEY"),
        timeout=CLAUDE_TIMEOUT,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "claude failed")
    return completed.stdout
