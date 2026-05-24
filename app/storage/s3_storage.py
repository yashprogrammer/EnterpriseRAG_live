"""S3 storage backend implementation."""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.storage.storage_backend import StorageBackend

logger = logging.getLogger(__name__)


class S3Storage(StorageBackend):
    """Stores bytes in S3 at s3://bucket/key."""

    def __init__(
        self,
        bucket: str | None = None,
        region: str | None = None,
        client: Any | None = None,
    ):
        self.bucket = bucket or settings.s3_cache_bucket
        self.region = region or settings.aws_region
        if not self.bucket:
            raise RuntimeError("S3 cache bucket is required")

        if client is not None:
            self.client = client
            return

        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for S3 storage backend") from exc

        self.client = boto3.client("s3", region_name=self.region)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:  # pragma: no cover - backend-specific failure decoding
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            logger.exception("S3 exists check failed for key=%s", key)
            return False

    def save_bytes(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def read_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def url_for(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"