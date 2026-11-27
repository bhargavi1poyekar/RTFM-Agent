"""
Tests for chunk IDs, the context line / metadata built for each chunk, and
the ChromaDB VectorStore. A tiny bag-of-words FakeEmbedder replaces the real
model so these run offline in about a second.
"""

import pytest

from tests.fakes import FakeEmbedder
from src.rtfm_agent.embedding.vector_store import (
    VectorStore,
    chunk_metadata,
    context_header,
    document_text,
    parse_doc_url,
)
from src.rtfm_agent.ingestion.base import DocumentChunk, make_chunk_id
from src.rtfm_agent.ingestion.boto3_scraper import Boto3Scraper

BASE = "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services"


def make_chunk(content: str, url: str = f"{BASE}/s3/client/put_object.html",
               section: str = "Overview", code_blocks=None) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=make_chunk_id(url, content),
        content=content,
        source_url=url,
        title="put_object - Boto3 1.44.11 documentation",
        section=section,
        metadata={},
        code_blocks=code_blocks or [],
        created_at="2026-11-25T00:00:00",
    )


@pytest.fixture
def store(tmp_path) -> VectorStore:
    return VectorStore(FakeEmbedder(), persist_dir=str(tmp_path / "chroma"),
                       collection_name="test_docs")


# ---------------------------------------------------------------- chunk IDs

def test_chunk_id_is_deterministic() -> None:
    assert make_chunk_id("u", "text") == make_chunk_id("u", "text")


def test_chunks_sharing_a_long_prefix_get_different_ids() -> None:
    # Regression: IDs used to hash only the first 100 chars, so 227 real
    # chunks collided and would have overwritten each other in the index.
    prefix = "**Parameters** " * 10
    assert make_chunk_id("u", prefix + "Bucket") != make_chunk_id("u", prefix + "Key")


def test_same_text_on_different_pages_gets_different_ids() -> None:
    assert make_chunk_id("page-a", "same") != make_chunk_id("page-b", "same")


def test_scraper_ids_match_make_chunk_id() -> None:
    scraper = Boto3Scraper(output_dir="data/raw", requests_per_second=1000)
    meta = {"url": f"{BASE}/s3.html", "title": "S3", "scraped_at": ""}
    chunk = scraper._create_chunk("  some content  ", meta, "Client", [])
    assert chunk.chunk_id == make_chunk_id(meta["url"], chunk.content)


# ------------------------------------------------------- context & metadata

@pytest.mark.parametrize("url, expected", [
    (f"{BASE}/s3.html", ("s3", "landing", "s3")),
    (f"{BASE}/s3/client/put_object.html", ("s3", "client", "put_object")),
    (f"{BASE}/ec2/paginator/DescribeInstances.html", ("ec2", "paginator", "DescribeInstances")),
    (f"{BASE}/s3/bucket/upload_file.html#anchor", ("s3", "bucket", "upload_file")),
    (f"{BASE}/s3/client.html", ("s3", "client", "client")),
    ("https://boto3.amazonaws.com/v1/documentation/api/latest/guide/quickstart.html",
     ("", "other", "quickstart")),
])
def test_parse_doc_url(url, expected) -> None:
    info = parse_doc_url(url)
    assert (info["service"], info["page_type"], info["page"]) == expected


@pytest.mark.parametrize("url, section, expected", [
    (f"{BASE}/s3/client/put_object.html", "Overview", "s3 > client > put_object"),
    (f"{BASE}/s3.html", "Overview", "s3"),
    (f"{BASE}/s3.html", "Paginators", "s3 > Paginators"),
    (f"{BASE}/s3/client.html", "Overview", "s3 > client"),
])
def test_context_header(url, section, expected) -> None:
    assert context_header(make_chunk("x", url=url, section=section)) == expected


def test_document_text_puts_context_before_content() -> None:
    chunk = make_chunk("Uploads an object.")
    assert document_text(chunk) == "s3 > client > put_object\n\nUploads an object."


def test_metadata_is_flat_scalars_and_cleans_title() -> None:
    meta = chunk_metadata(make_chunk("x", code_blocks=["print(1)", "print(2)"]))
    assert all(isinstance(v, (str, int, float, bool)) for v in meta.values())
    assert meta["title"] == "put_object"
    assert meta["service"] == "s3"
    assert meta["num_code_blocks"] == 2 and meta["has_code"] is True


# -------------------------------------------------------------- VectorStore

def test_empty_store_search_returns_nothing(store) -> None:
    assert store.count() == 0
    assert store.search("anything") == []


def test_add_and_search_finds_the_relevant_chunk(store) -> None:
    store.add_chunks([
        make_chunk("Upload a file to a bucket with put_object."),
        make_chunk("Start and stop virtual machine instances.",
                   url=f"{BASE}/ec2/client/start_instances.html"),
        make_chunk("Create a queue and send messages to it.",
                   url=f"{BASE}/sqs/client/create_queue.html"),
    ])
    assert store.count() == 3

    results = store.search("how do I upload a file to a bucket", top_k=2)
    assert len(results) == 2
    assert results[0].metadata["service"] == "s3"
    assert results[0].text.startswith("s3 > client > put_object")
    assert results[0].score >= results[1].score
    assert -1.0 <= results[1].score <= 1.0


def test_search_can_filter_by_service(store) -> None:
    store.add_chunks([
        make_chunk("Upload a file to a bucket."),
        make_chunk("Upload a file to a bucket.",
                   url=f"{BASE}/ec2/client/import_image.html"),
    ])
    results = store.search("upload a file", where={"service": "ec2"})
    assert [r.metadata["service"] for r in results] == ["ec2"]


def test_rerunning_is_idempotent_and_skips_existing(store) -> None:
    chunks = [make_chunk(f"Chunk number {i} about buckets.") for i in range(5)]
    first = store.add_chunks(chunks, batch_size=2)
    calls_after_first = store.embedder.calls
    second = store.add_chunks(chunks, batch_size=2)

    assert first == {"received": 5, "added": 5, "skipped": 0}
    assert second == {"received": 5, "added": 0, "skipped": 5}
    assert store.embedder.calls == calls_after_first  # nothing re-embedded
    assert store.count() == 5


def test_duplicate_chunks_in_input_are_stored_once(store) -> None:
    chunk = make_chunk("Identical text on the same page.")
    stats = store.add_chunks([chunk, chunk])
    assert stats["received"] == 1
    assert store.count() == 1


def test_ids_come_from_content_not_stored_chunk_id(store) -> None:
    # chunks.json files scraped before the ID fix carry prefix-based IDs that
    # can collide; the store must recompute them from URL + full text.
    prefix = "**Parameters** " * 10
    a = make_chunk(prefix + "Bucket (string) the bucket name.")
    b = make_chunk(prefix + "Key (string) the object key.")
    a.chunk_id = b.chunk_id = "old-colliding-id"
    store.add_chunks([a, b])
    assert store.count() == 2


def test_index_persists_on_disk(tmp_path) -> None:
    path = str(tmp_path / "chroma")
    VectorStore(FakeEmbedder(), persist_dir=path, collection_name="persist_test").add_chunks(
        [make_chunk("Persist me.")])
    reopened = VectorStore(FakeEmbedder(), persist_dir=path, collection_name="persist_test")
    assert reopened.count() == 1


def test_reset_empties_the_collection(store) -> None:
    store.add_chunks([make_chunk("Soon to be gone.")])
    store.reset()
    assert store.count() == 0
