"""Test doubles shared by several test files."""
import hashlib
import math
import re
from typing import List


class FakeEmbedder:
    """Hashes each word into one of 64 buckets; normalised like the real one."""

    dims = 64

    def __init__(self):
        self.calls = 0

    def _vec(self, text: str) -> List[float]:
        v = [0.0] * self.dims
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dims] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts, show_progress=False):
        self.calls += 1
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)
