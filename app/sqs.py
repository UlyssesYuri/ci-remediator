import os

import boto3


def queue_url() -> str | None:
    url = os.environ.get("SQS_QUEUE_URL", "").strip()
    return url or None


def client():
    kwargs: dict = {"region_name": os.environ.get("AWS_REGION", "us-east-1")}
    endpoint = os.environ.get("SQS_ENDPOINT_URL", "").strip()
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client("sqs", **kwargs)


def send_message(body: str) -> str:
    url = queue_url()
    if not url:
        raise RuntimeError("SQS_QUEUE_URL is not set")
    response = client().send_message(QueueUrl=url, MessageBody=body)
    return response["MessageId"]
