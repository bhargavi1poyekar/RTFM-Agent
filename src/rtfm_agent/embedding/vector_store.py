# src/rtfm_agent/embedding/vector_store.py
"""
Stores chunk vectors in ChromaDB and searches them.

Each chunk is embedded together with a short context line built from its
URL (e.g. "s3 > client > put_object"). The scraped section labels are
almost all "Overview", so without this line a chunk that just says
"Parameters: Bucket (string)..." gives no hint which API it belongs to.
"""
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from config.logging import logger
from config.settings import settings
from src.rtfm_agent.ingestion.base import DocumentChunk, make_chunk_id

_SERVICES_PATH = re.compile(r"/reference/services/(.+?)(?:\.html)?$")
_TITLE_SUFFIX = re.compile(r"\s+-\s+Boto3\b.*$")


def parse_doc_url(url: str) -> Dict[str, str]:
    """
    Pull service / page type / page name out of a Boto3 docs URL.

    .../reference/services/s3.html                    -> s3, landing, s3
    .../reference/services/s3/client/put_object.html  -> s3, client, put_object
    .../reference/services/ec2/paginator/X.html       -> ec2, paginator, X
    .../reference/services/s3/bucket/upload_file.html -> s3, bucket, upload_file
    anything else                                     -> "", other, last path part
    """
    match = _SERVICES_PATH.search(url.split("#")[0].split("?")[0])
    if not match:
        last = url.rstrip("/").split("/")[-1].removesuffix(".html")
        return {"service": "", "page_type": "other", "page": last}

    parts = match.group(1).split("/")
    if len(parts) == 1:
        return {"service": parts[0], "page_type": "landing", "page": parts[0]}
    if len(parts) == 2:
        # e.g. s3/client.html, ec2/instance.html
        return {"service": parts[0], "page_type": parts[1], "page": parts[1]}
    return {"service": parts[0], "page_type": parts[1], "page": parts[-1]}


def context_header(chunk: DocumentChunk) -> str:
    """Short breadcrumb prepended to the chunk before embedding."""
    info = parse_doc_url(chunk.source_url)
    if info["page_type"] == "landing":
        crumbs = [info["service"]]
    elif info["page_type"] == "other":
        crumbs = [info["page"]]
    else:  # dict.fromkeys drops the repeat in e.g. "s3 > client > client"
        crumbs = list(dict.fromkeys([info["service"], info["page_type"], info["page"]]))
    if chunk.section and chunk.section != "Overview":
        crumbs.append(chunk.section)
    return " > ".join(crumbs)


def document_text(chunk: DocumentChunk) -> str:
    """Exactly what gets embedded and stored: context line + chunk text."""
    header = context_header(chunk)
    return f"{header}\n\n{chunk.content}" if header else chunk.content


def chunk_metadata(chunk: DocumentChunk) -> Dict[str, Any]:
    """Flat metadata for filtering and citations (Chroma allows only scalars)."""
    info = parse_doc_url(chunk.source_url)
    return {
        "source_url": chunk.source_url,
        "title": _TITLE_SUFFIX.sub("", chunk.title or ""),
        "section": chunk.section or "",
        "service": info["service"],
        "page_type": info["page_type"],
        "page": info["page"],
        "num_code_blocks": len(chunk.code_blocks),
        "has_code": bool(chunk.code_blocks),
        "scraped_at": chunk.created_at or "",
    }


@dataclass
class SearchResult:
    chunk_id: str
    text: str
    metadata: Dict[str, Any]
    score: float  # cosine similarity, higher is better (1.0 = identical)


class VectorStore:
    """ChromaDB collection of chunk vectors, persisted on disk."""

    def __init__(
        self,
        embedder,
        persist_dir: Optional[str] = None,
        collection_name: Optional[str] = None,
    ):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        # With telemetry disabled nothing is sent, but chromadb 0.5.0 still
        # calls the (newer, incompatible) posthog client and logs an ERROR
        # on every start. Silence that one logger.
        logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

        self.embedder = embedder
        self.client = chromadb.PersistentClient(
            path=persist_dir or settings.CHROMADB_PERSIST_DIR,
            # Chroma otherwise sends usage telemetry to an external service.
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
        )
        self.collection_name = collection_name or settings.COLLECTION_NAME
        self.collection = self._get_collection()

    def _get_collection(self):
        # Cosine distance to match the normalised embeddings.
        return self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        return self.collection.count()

    def reset(self) -> None:
        """Delete and recreate the collection (drops stale chunks)."""
        self.client.delete_collection(self.collection_name)
        self.collection = self._get_collection()

    def existing_ids(self, ids: List[str]) -> set:
        if not ids:
            return set()
        return set(self.collection.get(ids=ids, include=[])["ids"])

    def add_chunks(
        self,
        chunks: Iterable[DocumentChunk],
        batch_size: int = 256,
        skip_existing: bool = True,
        show_progress: bool = False,
    ) -> Dict[str, int]:
        """
        Embed and upsert chunks. Safe to re-run: IDs come from URL + text,
        so an unchanged chunk keeps its ID and is skipped (or overwritten
        with an identical vector if skip_existing=False).
        """
        # Drop repeated IDs (identical text on the same page) up front;
        # Chroma rejects duplicate IDs inside one upsert.
        unique: Dict[str, DocumentChunk] = {}
        for chunk in chunks:
            unique.setdefault(make_chunk_id(chunk.source_url, chunk.content), chunk)

        items = list(unique.items())
        stats = {"received": len(items), "added": 0, "skipped": 0}

        for start in range(0, len(items), batch_size):
            batch = items[start:start + batch_size]
            if skip_existing:
                have = self.existing_ids([cid for cid, _ in batch])
                stats["skipped"] += len(have)
                batch = [(cid, c) for cid, c in batch if cid not in have]
            if not batch:
                continue

            texts = [document_text(c) for _, c in batch]
            self.collection.upsert(
                ids=[cid for cid, _ in batch],
                documents=texts,
                embeddings=self.embedder.embed_documents(texts),
                metadatas=[chunk_metadata(c) for _, c in batch],
            )
            stats["added"] += len(batch)

            if show_progress:
                done = min(start + batch_size, len(items))
                logger.info(f"Indexed {done}/{len(items)} chunks")

        return stats

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """Return the top_k most similar chunks, best first."""
        if self.count() == 0:
            return []
        result = self.collection.query(
            query_embeddings=[self.embedder.embed_query(query)],
            n_results=min(top_k or settings.RETRIEVAL_TOP_K, self.count()),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        return [
            SearchResult(
                chunk_id=cid,
                text=doc,
                metadata=meta,
                score=1.0 - dist,
            )
            for cid, doc, meta, dist in zip(
                result["ids"][0],
                result["documents"][0],
                result["metadatas"][0],
                result["distances"][0],
            )
        ]
