# src/rtfm_agent/retrieval/retriever.py
"""
Finds the doc chunks most relevant to a question.

Two searches, combined:
- Vector search (meaning): "make a bucket private" can match text that never
  uses those words.
- BM25 keyword search (exact words): "put_bucket_policy" or "FIFO" match the
  chunks that literally contain them, which embeddings can blur.

Their rankings are merged with Reciprocal Rank Fusion (RRF): each result
scores sum(1 / (60 + rank)) over the lists it appears in. RRF only uses
ranks, so the two very different score scales never need calibrating.

Results are then collapsed to one chunk per page, so five near-identical
chunks from the same page can't crowd out other useful pages.

Default is vector + one-per-page. Measured on eval/retrieval_questions.json
(scripts/eval_retrieval.py): hybrid won on the dev split (Recall@1 72% vs
64%) but lost on the held-out test split (53% vs 71%): keywords match
surface words ("AMI" -> create_image, "SSH" -> get_ssh_public_key) rather
than intent. One-per-page helped consistently (all 53: Recall@5 92.5% ->
96.2%). Hybrid stays available via mode="hybrid".
"""
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

from config.settings import settings
from src.rtfm_agent.embedding.vector_store import SearchResult, VectorStore

Mode = Literal["vector", "bm25", "hybrid"]

_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from get how i in into is it "
    "its me my of on or the this to using what when with you your".split()
)


# Words that name a service in a question -> the "service" metadata value
SERVICE_ALIASES = {
    "s3": "s3", "bucket": "s3", "buckets": "s3",
    "ec2": "ec2", "ebs": "ec2", "ami": "ec2",
    "iam": "iam",
    "lambda": "lambda",
    "dynamodb": "dynamodb", "dynamo": "dynamodb",
    "sqs": "sqs",
    "sns": "sns",
    "cloudwatch": "cloudwatch",
    "cloudformation": "cloudformation",
    "rds": "rds",
}


def detect_services(query: str) -> set:
    """AWS services a question names explicitly, e.g. {'ec2'}."""
    return {SERVICE_ALIASES[w] for w in re.findall(r"[a-z0-9]+", query.lower())
            if w in SERVICE_ALIASES}


def tokenize(text: str) -> List[str]:
    """Lowercase words; identifiers split on '_' ("put_object" -> put, object)."""
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS]


def page_key(url: str) -> str:
    """'.../reference/services/s3/client/put_object.html' -> 's3/client/put_object'."""
    path = url.split("#")[0].split("/reference/services/")[-1]
    return path.removesuffix(".html")


class BM25Index:
    """Classic Okapi BM25 over an in-memory list of documents."""

    def __init__(self, docs: List[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.doc_len: List[int] = []
        self.postings: Dict[str, List[tuple]] = defaultdict(list)  # term -> [(doc, tf)]
        for i, doc in enumerate(docs):
            counts = Counter(tokenize(doc))
            self.doc_len.append(sum(counts.values()))
            for term, tf in counts.items():
                self.postings[term].append((i, tf))
        self.n_docs = len(docs)
        self.avg_len = sum(self.doc_len) / max(self.n_docs, 1)

    def idf(self, term: str) -> float:
        df = len(self.postings.get(term, ()))
        return math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int) -> List[tuple]:
        """Return [(doc_index, score)] best first."""
        scores: Dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            idf = self.idf(term)
            for doc, tf in self.postings.get(term, ()):
                norm = self.k1 * (1 - self.b + self.b * self.doc_len[doc] / self.avg_len)
                scores[doc] += idf * tf * (self.k1 + 1) / (tf + norm)
        return sorted(scores.items(), key=lambda x: -x[1])[:top_k]


def reciprocal_rank_fusion(
    rankings: List[List[str]], k: int = 60, weights: Optional[List[float]] = None
) -> List[tuple]:
    """Merge ranked ID lists: score(id) = sum w / (k + rank). Returns [(id, score)].
    A weight below 1 lets a list break ties without overruling the others."""
    weights = weights or [1.0] * len(rankings)
    scores: Dict[str, float] = defaultdict(float)
    for ranking, w in zip(rankings, weights):
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += w / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    metadata: Dict
    score: float  # cosine similarity (vector) / BM25 score / RRF score (hybrid)

    @property
    def page(self) -> str:
        return page_key(self.metadata.get("source_url", ""))


class Retriever:
    """
    Args:
        store: the VectorStore holding the embedded chunks
        mode: "vector", "bm25" or "hybrid" (both, fused with RRF)
        candidates: how many results each search contributes before fusion
        one_per_page: keep only the best chunk from each page
        service_filter: if the question names exactly one AWS service
            ("Stop an EC2 instance"), search only that service. Two or more
            ("allow S3 to trigger Lambda") -> no filter, either may hold
            the answer. Ignored when the caller passes its own `where`.
    """

    def __init__(
        self,
        store: VectorStore,
        mode: Mode = "vector",
        candidates: int = 30,
        one_per_page: bool = True,
        # Off by default: no gain on the dev set (MRR 0.796 -> 0.794)
        service_filter: bool = False,
        rrf_k: int = 60,
        bm25_weight: float = 1.0,
    ):
        self.store = store
        self.mode = mode
        self.candidates = candidates
        self.one_per_page = one_per_page
        self.service_filter = service_filter
        self.rrf_k = rrf_k
        self.bm25_weight = bm25_weight
        self._bm25: Optional[BM25Index] = None

    def _load_corpus(self) -> None:
        """Pull every stored chunk once and build the keyword index."""
        data = self.store.collection.get(include=["documents", "metadatas"])
        self._ids: List[str] = data["ids"]
        self._docs: Dict[str, tuple] = {
            cid: (doc, meta)
            for cid, doc, meta in zip(data["ids"], data["documents"], data["metadatas"])
        }
        self._bm25 = BM25Index(data["documents"])

    def _vector(self, query: str, n: int, where: Optional[Dict]) -> List[RetrievedChunk]:
        return [
            RetrievedChunk(r.chunk_id, r.text, r.metadata, r.score)
            for r in self.store.search(query, top_k=n, where=where)
        ]

    def _keyword(self, query: str, n: int, where: Optional[Dict]) -> List[RetrievedChunk]:
        if self._bm25 is None:
            self._load_corpus()
        hits = []
        # Over-fetch when filtering, since the filter is applied afterwards
        for idx, score in self._bm25.search(query, n * (10 if where else 1)):
            cid = self._ids[idx]
            doc, meta = self._docs[cid]
            if where and any(meta.get(k) != v for k, v in where.items()):
                continue
            hits.append(RetrievedChunk(cid, doc, meta, score))
            if len(hits) == n:
                break
        return hits

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        where: Optional[Dict] = None,
    ) -> List[RetrievedChunk]:
        """Top-k chunks for `query`, best first. `where` filters on metadata
        equality, e.g. {"service": "s3"}."""
        top_k = top_k or settings.RETRIEVAL_TOP_K
        n = max(self.candidates, top_k)
        if where is None and self.service_filter:
            services = detect_services(query)
            if len(services) == 1:
                where = {"service": services.pop()}

        if self.mode == "vector":
            ranked = self._vector(query, n, where)
        elif self.mode == "bm25":
            ranked = self._keyword(query, n, where)
        else:
            vec = self._vector(query, n, where)
            kw = self._keyword(query, n, where)
            by_id = {c.chunk_id: c for c in kw + vec}
            fused = reciprocal_rank_fusion(
                [[c.chunk_id for c in vec], [c.chunk_id for c in kw]],
                k=self.rrf_k, weights=[1.0, self.bm25_weight],
            )
            ranked = [
                RetrievedChunk(cid, by_id[cid].text, by_id[cid].metadata, score)
                for cid, score in fused
            ]

        if self.one_per_page:
            seen, unique = set(), []
            for chunk in ranked:
                if chunk.page not in seen:
                    seen.add(chunk.page)
                    unique.append(chunk)
            ranked = unique

        return ranked[:top_k]
