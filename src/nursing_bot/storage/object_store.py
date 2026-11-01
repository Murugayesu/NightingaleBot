from contextlib import asynccontextmanager
from typing import Any

from aiobotocore.session import get_session


class S3Backend:
    """Async client managing document file blobs in MinIO, AWS S3, or R2."""

    def __init__(
        self,
        endpoint_url: str | None,
        access_key_id: str | None,
        secret_access_key: str | None,
        bucket_name: str,
        region: str = "us-east-1",
    ):
        self.endpoint_url = endpoint_url
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket_name = bucket_name
        self.region = region
        self._session = get_session()

    @asynccontextmanager
    async def _client(self):
        async with self._session.create_client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region,
        ) as client:
            yield client

    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> str:
        async with self._client() as client:
            kwargs: dict[str, Any] = {
                "Bucket": self.bucket_name,
                "Key": key,
                "Body": data,
            }
            if content_type:
                kwargs["ContentType"] = content_type
            await client.put_object(**kwargs)
            return f"s3://{self.bucket_name}/{key}"

    async def download(self, key: str) -> bytes:
        async with self._client() as client:
            response = await client.get_object(Bucket=self.bucket_name, Key=key)
            async with response["Body"] as stream:
                return await stream.read()

    async def delete(self, key: str) -> bool:
        async with self._client() as client:
            await client.delete_object(Bucket=self.bucket_name, Key=key)
            return True

    async def list_keys(self, prefix: str = "", limit: int = 1000) -> list[str]:
        async with self._client() as client:
            response = await client.list_objects_v2(
                Bucket=self.bucket_name, Prefix=prefix, MaxKeys=limit
            )
            contents = response.get("Contents", [])
            return [item["Key"] for item in contents]
