import os

PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TERM")


def sandbox_env(*extra: str) -> dict:
    """Only these names. Claude and the test commands never see
    GITHUB_TOKEN, DATABASE_URL, or AWS keys."""
    names = PASSTHROUGH + extra
    return {name: os.environ[name] for name in names if name in os.environ}
