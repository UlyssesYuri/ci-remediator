import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["AWS_REGION"] = "us-east-1"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "test"
os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
os.environ.pop("SQS_ENDPOINT_URL", None)

from moto import mock_aws  # noqa: E402

_mock = mock_aws()
_mock.start()

import boto3  # noqa: E402

_queue = boto3.client("sqs", region_name="us-east-1").create_queue(
    QueueName="ci-remediator"
)
os.environ["SQS_QUEUE_URL"] = _queue["QueueUrl"]

from app.db import init_db, reset_engine  # noqa: E402

reset_engine()
init_db()
