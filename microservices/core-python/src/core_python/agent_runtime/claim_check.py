"""Claim-check codec: large payloads go to S3, only a reference goes into workflow history.

Model responses and tool outputs can be large. Temporal history should stay small (it is
replayed on every worker restart and has hard size limits), so any payload over the threshold
is stored in object storage and replaced by its key. Keys are the payload's SHA-256, so a
retried write stores the same object again harmlessly, and replay fetches the same bytes.

The codec runs in the client and the worker, outside the workflow sandbox.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from typing import Protocol

from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

ENCODING = b"wfk/claim-check-v1"
DEFAULT_THRESHOLD = 128 * 1024


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes: ...


class MemoryBlobStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.blobs[key] = data

    async def get(self, key: str) -> bytes:
        return self.blobs[key]


class S3BlobStore:
    """Any S3 API: SeaweedFS locally, Ceph RGW or cloud S3 in production."""

    def __init__(
        self, *, endpoint: str, bucket: str, access_key: str, secret_key: str, prefix: str = ""
    ) -> None:
        import boto3  # imported here: only workers and clients that offload need it

        self.bucket = bucket
        self.prefix = prefix
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
        )

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(
            self.client.put_object, Bucket=self.bucket, Key=self.prefix + key, Body=data
        )

    async def get(self, key: str) -> bytes:
        obj = await asyncio.to_thread(
            self.client.get_object, Bucket=self.bucket, Key=self.prefix + key
        )
        return obj["Body"].read()


class ClaimCheckCodec(PayloadCodec):
    def __init__(self, store: BlobStore, threshold: int = DEFAULT_THRESHOLD) -> None:
        self.store = store
        self.threshold = threshold

    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        out: list[Payload] = []
        for p in payloads:
            if p.ByteSize() <= self.threshold:
                out.append(p)
                continue
            raw = p.SerializeToString()
            key = "payloads/" + hashlib.sha256(raw).hexdigest()
            await self.store.put(key, raw)
            out.append(Payload(metadata={"encoding": ENCODING}, data=key.encode()))
        return out

    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        out: list[Payload] = []
        for p in payloads:
            if p.metadata.get("encoding") != ENCODING:
                out.append(p)
                continue
            raw = await self.store.get(p.data.decode())
            restored = Payload()
            restored.ParseFromString(raw)
            out.append(restored)
        return out
