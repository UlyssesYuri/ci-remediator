import io
import zipfile

from app.agent.state import LOG_TAIL
from app.s3 import bucket_name, client


def tail(text: str) -> str:
    return text[-LOG_TAIL:]


def log_text_from_zip(blob: bytes) -> str:
    parts = []
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts.append(archive.read(info).decode("utf-8", errors="replace"))
    return tail("\n".join(parts))


def read_archived_logs(log_s3_key: str) -> str:
    """Never calls GitHub. Reads only the zip already in S3."""
    response = client().get_object(Bucket=bucket_name(), Key=log_s3_key)
    return log_text_from_zip(response["Body"].read())
