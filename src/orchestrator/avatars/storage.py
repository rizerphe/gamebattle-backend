"""Avatar bytes in S3-compatible object storage."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

import boto3
from botocore.exceptions import ClientError


@dataclass(frozen=True)
class StoredAvatar:
    body: bytes
    content_type: str


class AvatarStore(Protocol):
    async def ensure_bucket(self) -> None:
        """Create the bucket when missing."""

    async def get(self, key: str) -> StoredAvatar | None:
        """Cached avatar bytes, or ``None`` when absent."""

    async def put(self, key: str, body: bytes, content_type: str) -> None:
        """Write or replace an avatar object."""


class S3AvatarStore:
    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        access_key: str,
        secret_key: str,
        bucket: str,
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
                return
            except ClientError as error:
                code = str(error.response.get("Error", {}).get("Code"))
                if code in {"301", "PermanentRedirect"}:
                    raise RuntimeError(
                        f"bucket {self._bucket!r} exists in a different region than "
                        f"S3_REGION={self._region!r} / S3_ENDPOINT; make them match "
                        "the bucket's region"
                    ) from error
                if code not in {"404", "NoSuchBucket", "NotFound"}:
                    raise
            if self._region == "us-east-1":
                self._client.create_bucket(Bucket=self._bucket)
            else:
                self._client.create_bucket(
                    Bucket=self._bucket,
                    CreateBucketConfiguration={"LocationConstraint": self._region},
                )

        await asyncio.to_thread(_ensure)

    async def get(self, key: str) -> StoredAvatar | None:
        def _get() -> StoredAvatar | None:
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=key)
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if code in {"NoSuchKey", "404", "NotFound"}:
                    return None
                raise
            body = response["Body"].read()
            content_type = response.get("ContentType") or "image/jpeg"
            if not isinstance(body, bytes) or not body:
                return None
            if not isinstance(content_type, str) or not content_type.startswith("image/"):
                content_type = "image/jpeg"
            return StoredAvatar(body=body, content_type=content_type)

        return await asyncio.to_thread(_get)

    async def put(self, key: str, body: bytes, content_type: str) -> None:
        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
            )

        await asyncio.to_thread(_put)


def avatar_object_key(firebase_uid: str) -> str:
    return f"firebase/{firebase_uid}"


def open_avatar_store(settings) -> AvatarStore:
    return S3AvatarStore(
        endpoint=settings.s3_endpoint,
        region=settings.s3_region,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        bucket=settings.s3_avatars_bucket,
    )
