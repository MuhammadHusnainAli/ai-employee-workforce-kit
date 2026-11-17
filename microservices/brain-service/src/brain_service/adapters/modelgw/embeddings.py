"""Embeddings: through the Model Gateway in production, a deterministic hash locally.

Every embedding has DIMS dimensions, matching the `vector(1024)` column in brain_db.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from typing import Protocol

import httpx

DIMS = 1024
_WORD = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dims: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """No network, no model: each word (and word pair) is hashed into a dimension, then the
    vector is L2-normalised. Texts that share words land close together. For local runs and
    tests; it knows nothing about meaning beyond shared words."""

    dims = DIMS

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        words = _WORD.findall(text.lower())
        for token in [*words, *(f"{a} {b}" for a, b in itertools.pairwise(words))]:
            h = hashlib.blake2b(token.encode(), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "big") % self.dims
            vec[idx] += 1.0 if h[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class GatewayEmbedder:
    """OpenAI-format /embeddings on the Model Gateway (LiteLLM)."""

    dims = DIMS

    def __init__(self, base_url: str, api_key: str, model: str, *, timeout_s: float = 5.0) -> None:
        self.model = model
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        r = await self.client.post(
            "/embeddings", json={"model": self.model, "input": texts, "dimensions": self.dims}
        )
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        vectors = [d["embedding"] for d in data]
        if any(len(v) != self.dims for v in vectors):
            raise ValueError(f"the gateway returned embeddings that are not {self.dims}-d")
        return vectors

    async def aclose(self) -> None:
        await self.client.aclose()
