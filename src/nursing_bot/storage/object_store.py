"""Async S3-compatible object storage (MinIO locally, AWS S3 or Cloudflare R2 in production)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from aiobotocore.config import AioConfig
from aiobotocore.session import get_session


class S3Backend:
    """Stores raw uploaded files. One short-lived client per operation keeps it
    safe to share across requests and event loops (API and worker)."""

    def __init__(
        self,
        endpoint_url: str | None,
        access_key_id: str | None,
        secret_access_key: str | None,
        bucket_name: str,
        region: str = "us-east-1",
    ):
        self.endpoint_url = endpoint_url or None
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket_name = bucket_name
        self.region = region
        self._session = get_session()
        self._config = AioConfig(
            connect_timeout=10,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
        )

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        async with self._session.create_client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region,
            config=self._config,
        ) as client:
            yield client

    async def upload(
        self, key: str, data: bytes, content_type: str | None = None
    ) -> str:
        kwargs: dict[str, Any] = {"Bucket": self.bucket_name, "Key": key, "Body": data}
        if content_type:
            kwargs["ContentType"] = content_type
        async with self._client() as client:
            await client.put_object(**kwargs)
        return f"s3://{self.bucket_name}/{key}"

    async def download(self, key: str) -> bytes:
        async with self._client() as client:
            response = await client.get_object(Bucket=self.bucket_name, Key=key)
            async with response["Body"] as stream:
                data: bytes = await stream.read()
                return data

    async def delete(self, key: str) -> None:
        async with self._client() as client:
            await client.delete_object(Bucket=self.bucket_name, Key=key)

    async def check(self) -> None:
        """Raise if the bucket is unreachable (used by the health endpoint)."""
        async with self._client() as client:
            await client.head_bucket(Bucket=self.bucket_name)
