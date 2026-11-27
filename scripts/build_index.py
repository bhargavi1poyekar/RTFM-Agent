# scripts/build_index.py
"""
Embed the scraped chunks and store them in ChromaDB.

    python scripts/build_index.py              # add new chunks, skip ones already indexed
    python scripts/build_index.py --rebuild    # start from an empty collection
    python scripts/build_index.py --search "upload a file to s3"   # quick check
"""
import argparse
import json
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config.logging import logger
from config.settings import settings
from src.rtfm_agent.embedding.embedder import Embedder
from src.rtfm_agent.embedding.vector_store import VectorStore, document_text
from src.rtfm_agent.ingestion.base import DocumentChunk
from src.rtfm_agent.ingestion.validator import ChunkValidator


def load_chunks(path: str):
    with open(path, encoding="utf-8") as f:
        return [DocumentChunk.from_dict(d) for d in json.load(f)]


def report_truncation(embedder: Embedder, chunks) -> None:
    """How many chunks (with their context line) exceed the model's token limit."""
    limit = embedder.max_tokens
    over = 0
    lost_tokens = total_tokens = 0
    for chunk in chunks:
        # +2 for the [CLS]/[SEP] tokens the model adds around every input
        n = embedder.count_tokens(document_text(chunk)) + 2
        total_tokens += n
        if n > limit:
            over += 1
            lost_tokens += n - limit
    logger.info(
        f"Token check: {over}/{len(chunks)} chunks over {limit} tokens "
        f"({over / max(len(chunks), 1):.1%}); "
        f"{lost_tokens / max(total_tokens, 1):.2%} of all tokens truncated"
    )


def build_index(chunks_path: str, rebuild: bool, persist_dir: Optional[str] = None,
                min_tokens: Optional[int] = None, max_tokens: Optional[int] = None) -> VectorStore:
    raw = load_chunks(chunks_path)
    valid = ChunkValidator(min_length=min_tokens, max_length=max_tokens).filter_valid(raw)
    logger.info(f"Loaded {len(raw)} chunks, {len(valid)} passed validation")

    embedder = Embedder()
    report_truncation(embedder, valid)

    store = VectorStore(embedder, persist_dir=persist_dir)
    if rebuild:
        logger.info("Rebuilding: deleting existing collection")
        store.reset()

    start = time.time()
    stats = store.add_chunks(valid, show_progress=True)
    logger.info(
        f"Indexing done in {time.time() - start:.0f}s: {stats}; "
        f"collection now holds {store.count()} chunks"
    )
    return store


def show_search(store: VectorStore, query: str) -> None:
    print(f"\nQuery: {query}")
    for i, r in enumerate(store.search(query), 1):
        preview = " ".join(r.text.split())[:140]
        print(f"{i}. [{r.score:.3f}] {r.metadata['source_url']}\n   {preview}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--chunks", default=os.path.join(settings.RAW_DATA_DIR, "chunks.json"))
    parser.add_argument("--rebuild", action="store_true", help="drop the collection first")
    parser.add_argument("--search", action="append", default=[],
                        help="only run this query against the existing index (repeatable)")
    parser.add_argument("--persist-dir", default=None,
                        help="index folder (default: CHROMADB_PERSIST_DIR), e.g. for experiments")
    parser.add_argument("--min-tokens", type=int, default=None,
                        help="validator minimum (default: CHUNK_MIN_TOKENS)")
    parser.add_argument("--max-tokens", type=int, default=None,
                        help="validator maximum (default: CHUNK_MAX_TOKENS)")
    args = parser.parse_args()

    if args.search:
        store = VectorStore(Embedder(), persist_dir=args.persist_dir)
        logger.info(f"Collection holds {store.count()} chunks")
    else:
        store = build_index(args.chunks, args.rebuild, args.persist_dir,
                            args.min_tokens, args.max_tokens)

    for query in args.search:
        show_search(store, query)


if __name__ == "__main__":
    main()
