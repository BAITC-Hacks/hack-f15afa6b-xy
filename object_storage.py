"""Private S3-compatible storage for citizen media."""

import hashlib
import os
import re
from functools import lru_cache
from urllib.parse import urlparse

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError


MAX_MEDIA_BYTES = 12 * 1024 * 1024
EXTENSIONS = {
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
    "video/mp4": "mp4", "video/webm": "webm",
}


class ObjectStorageError(RuntimeError):
    pass


class ObjectStorage:
    def __init__(self, endpoint="", bucket="", region="us-east-1", prefix="pulse109", client=None):
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.region = region
        self.prefix = prefix.strip("/")
        self.enabled = bool(self.endpoint and self.bucket)
        if bool(self.endpoint) != bool(self.bucket):
            raise ValueError("P109_S3_ENDPOINT and P109_S3_BUCKET must be configured together")
        if not self.enabled:
            self.client = None
            return
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("P109_S3_ENDPOINT must use HTTPS")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket):
            raise ValueError("P109_S3_BUCKET is invalid")
        if not re.fullmatch(r"[A-Za-z0-9/_-]{1,100}", self.prefix):
            raise ValueError("P109_S3_PREFIX is invalid")
        self.client = client or boto3.client(
            "s3", endpoint_url=self.endpoint, region_name=self.region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                          request_checksum_calculation="when_required",
                          response_checksum_validation="when_required"),
        )

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("P109_S3_ENDPOINT", ""), os.environ.get("P109_S3_BUCKET", ""),
                   os.environ.get("P109_S3_REGION", "us-east-1"),
                   os.environ.get("P109_S3_PREFIX", "pulse109"))

    def status(self):
        return {"status": "configured" if self.enabled else "local_sqlite",
                "backend": "s3" if self.enabled else "sqlite"}

    def check(self):
        if not self.enabled:
            return self.status()
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError("Private object storage is unavailable") from error
        return {"status": "healthy", "backend": "s3"}

    def put_media(self, complaint_id, kind, mime_type, content):
        if not self.enabled:
            return None
        extension = EXTENSIONS.get(mime_type)
        if kind not in {"photo", "video"} or not extension or len(content) > MAX_MEDIA_BYTES:
            raise ObjectStorageError("Media object is invalid")
        key = f"{self.prefix}/complaints/{complaint_id}/{kind}.{extension}"
        try:
            self.client.put_object(
                Bucket=self.bucket, Key=key, Body=content, ContentType=mime_type,
                ContentLength=len(content),
                CacheControl="private, max-age=3600",
                Metadata={"sha256": hashlib.sha256(content).hexdigest()},
            )
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError("Could not save media to private object storage") from error
        return key

    def get_media(self, key):
        expected = f"{self.prefix}/complaints/"
        if not self.enabled or not key.startswith(expected):
            raise ObjectStorageError("Media object key is invalid")
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            if int(response.get("ContentLength", 0)) > MAX_MEDIA_BYTES:
                raise ObjectStorageError("Stored media exceeds the size limit")
            content = response["Body"].read(MAX_MEDIA_BYTES + 1)
        except (BotoCoreError, ClientError, KeyError) as error:
            raise ObjectStorageError("Could not read media from private object storage") from error
        if len(content) > MAX_MEDIA_BYTES:
            raise ObjectStorageError("Stored media exceeds the size limit")
        return content

    def delete(self, key):
        if not self.enabled or not key:
            return
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError("Could not delete media from private object storage") from error


@lru_cache(maxsize=1)
def object_storage():
    return ObjectStorage.from_env()
