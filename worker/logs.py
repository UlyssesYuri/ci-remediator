import httpx

from app.agent.github import github_token
from app.s3 import bucket_name, client, logs_key
from app.runs import set_log_s3_key


def fetch_logs(logs_url: str) -> bytes:
    # follow_redirects is the httpx name for the GitHub 302.
    # The Location dies in about a minute.
    response = httpx.get(
        logs_url,
        headers={
            "Authorization": f"Bearer {github_token()}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ci-remediator",
        },
        follow_redirects=True,
        timeout=60,
    )
    response.raise_for_status()
    return response.content


def archive_logs(body: dict) -> str:
    key = logs_key(body["full_name"], int(body["github_run_id"]))
    client().put_object(Bucket=bucket_name(), Key=key, Body=fetch_logs(body["logs_url"]))
    set_log_s3_key(body["run_id"], key)
    return key
