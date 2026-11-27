# src/rtfm_agent/retrieval/evaluation.py
"""
Retrieval metrics over a hand-labelled question set.

Each question lists the doc pages that correctly answer it (several can be
right, e.g. s3/client/upload_file and s3/bucket/upload_file). A retrieved
chunk counts as a hit if it comes from any of those pages.

- Recall@k: share of questions with at least one correct page in the top k.
- MRR (mean reciprocal rank): average of 1/rank of the first correct result
  (1.0 = always first, 0.5 = typically second, 0 = never found in the top k).
"""
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


@dataclass
class Question:
    id: int
    q: str
    pages: List[str]
    split: str = "dev"


def load_questions(path: str, split: Optional[str] = None) -> List[Question]:
    with open(path, encoding="utf-8") as f:
        qs = [Question(**d) for d in json.load(f)]
    return [q for q in qs if split in (None, "all") or q.split == split]


def first_hit_rank(retrieved_pages: Sequence[str], relevant: Sequence[str]) -> Optional[int]:
    """1-based rank of the first relevant page, or None if absent."""
    relevant = set(relevant)
    for rank, page in enumerate(retrieved_pages, start=1):
        if page in relevant:
            return rank
    return None


@dataclass
class EvalResult:
    k: int
    n: int = 0
    hits_at_1: int = 0
    hits_at_k: int = 0
    rr_sum: float = 0.0
    misses: List[Dict] = field(default_factory=list)

    @property
    def recall_at_1(self) -> float:
        return self.hits_at_1 / self.n if self.n else 0.0

    @property
    def recall_at_k(self) -> float:
        return self.hits_at_k / self.n if self.n else 0.0

    @property
    def mrr(self) -> float:
        return self.rr_sum / self.n if self.n else 0.0


def evaluate(retriever, questions: List[Question], k: int = 5) -> EvalResult:
    """Run every question through `retriever.search` and score the top k."""
    result = EvalResult(k=k)
    for q in questions:
        pages = [c.page for c in retriever.search(q.q, top_k=k)]
        rank = first_hit_rank(pages, q.pages)
        result.n += 1
        if rank is not None:
            result.hits_at_k += 1
            result.hits_at_1 += rank == 1
            result.rr_sum += 1.0 / rank
        else:
            result.misses.append({"id": q.id, "q": q.q, "expected": q.pages, "got": pages})
    return result
