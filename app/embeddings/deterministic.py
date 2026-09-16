"""Deterministic, network-free embedding provider.

Implements a classic "hashing trick" / random-projection embedding: each
distinct token is mapped to a fixed pseudo-random unit vector seeded from
its SHA-256 hash, and a text's embedding is the term-frequency-weighted sum
of its tokens' vectors, L2-normalized. This gives two properties without any
ML model or network call:

1. Repeatability: the same text always produces the same vector.
2. Lexical similarity: texts that share vocabulary end up closer in cosine
   distance than texts that do not, which is what makes pgvector ranking
   tests meaningful.

It is NOT semantically meaningful the way a trained embedding model is --
two texts about the same incident using different wording will not
necessarily be close. That tradeoff (reproducibility over semantic
quality) is intentional and documented in DESIGN_DECISIONS.md.
"""

import hashlib
import math
import random
import re
from functools import lru_cache

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=4096)
def _token_vector(token: str, dimensions: int) -> tuple[float, ...]:
    seed_bytes = hashlib.sha256(token.encode("utf-8")).digest()[:8]
    seed = int.from_bytes(seed_bytes, "big")
    rng = random.Random(seed)
    raw = [rng.gauss(0.0, 1.0) for _ in range(dimensions)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return tuple(v / norm for v in raw)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class DeterministicEmbeddingProvider:
    def __init__(self, dimensions: int = 1536) -> None:
        self.dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        return self.embed_sync(text)

    def embed_sync(self, text: str) -> list[float]:
        tokens = _tokenize(text) or ["__empty__"]
        acc = [0.0] * self.dimensions
        counts: dict[str, int] = {}
        for tok in tokens:
            counts[tok] = counts.get(tok, 0) + 1
        for tok, count in counts.items():
            vec = _token_vector(tok, self.dimensions)
            for i in range(self.dimensions):
                acc[i] += vec[i] * count
        norm = math.sqrt(sum(v * v for v in acc)) or 1.0
        return [v / norm for v in acc]
