import os

import boto3


def bucket_name() -> str:
    name = os.environ.get("AWS_S3_BUCKET", "").strip()
    if not name:
        raise RuntimeError("AWS_S3_BUCKET is not set")
    return name


def logs_key(full_name: str, github_run_id: int) -> str:
    return f"runs/{full_name}/{github_run_id}/logs.zip"


def client():
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
