"""
Tests for the retriever (BM25, rank fusion, one-chunk-per-page, service
filter) and the evaluation metrics. Uses FakeEmbedder + a temp Chroma dir.
"""
import json

import pytest

from src.rtfm_agent.embedding.vector_store import VectorStore
from src.rtfm_agent.ingestion.base import DocumentChunk, make_chunk_id
from src.rtfm_agent.retrieval.evaluation import (
    Question,
    evaluate,
    first_hit_rank,
    load_questions,
)
from src.rtfm_agent.retrieval.retriever import (
    BM25Index,
    Retriever,
    detect_services,
    page_key,
    reciprocal_rank_fusion,
    tokenize,
)
from tests.fakes import FakeEmbedder

BASE = "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services"


def chunk(page: str, content: str) -> DocumentChunk:
    url = f"{BASE}/{page}.html"
    return DocumentChunk(
        chunk_id=make_chunk_id(url, content), content=content, source_url=url,
        title=page, section="Overview", metadata={}, code_blocks=[], created_at="",
    )


CORPUS = [
    chunk("s3/client/upload_file", "Upload a file to an S3 bucket from local disk."),
    chunk("s3/client/upload_file", "Upload a file: Filename, Bucket and Key parameters."),
    chunk("s3/client/put_bucket_policy", "Apply a bucket policy document to a bucket."),
    chunk("ec2/client/stop_instances", "Stops an Amazon EBS-backed instance."),
    chunk("rds/client/stop_db_instance", "Stops a DB instance temporarily."),
    chunk("sqs/client/send_message", "Delivers a message to the specified queue."),
]


@pytest.fixture
def store(tmp_path) -> VectorStore:
    s = VectorStore(FakeEmbedder(), persist_dir=str(tmp_path / "chroma"),
                    collection_name="retrieval_test")
    s.add_chunks(CORPUS)
    return s


# ------------------------------------------------------------- building blocks

def test_tokenize_splits_identifiers_and_drops_stopwords() -> None:
    assert tokenize("How do I call put_object?") == ["call", "put", "object"]


def test_page_key() -> None:
    assert page_key(f"{BASE}/s3/client/put_object.html#anchor") == "s3/client/put_object"
    assert page_key(f"{BASE}/s3.html") == "s3"


def test_detect_services() -> None:
    assert detect_services("Stop an EC2 instance") == {"ec2"}
    assert detect_services("Allow S3 to trigger my Lambda") == {"s3", "lambda"}
    assert detect_services("create a queue") == set()


def test_bm25_ranks_rare_matching_term_first() -> None:
    index = BM25Index(["the cat sat", "the dog sat", "a fifo queue"])
    assert index.search("fifo", top_k=3)[0][0] == 2
    assert index.search("unknownword", top_k=3) == []


def test_bm25_prefers_shorter_doc_for_same_term_count() -> None:
    index = BM25Index(["queue " + "filler " * 50, "queue"])
    assert index.search("queue", top_k=2)[0][0] == 1


def test_rrf_rewards_items_ranked_well_in_both_lists() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]])
    assert [doc for doc, _ in fused][0] == "b"
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)


# ------------------------------------------------------------------- retriever

@pytest.mark.parametrize("mode", ["vector", "bm25", "hybrid"])
def test_each_mode_finds_the_obvious_page(store, mode) -> None:
    r = Retriever(store, mode=mode, service_filter=False)
    assert r.search("send a message to a queue", top_k=3)[0].page == "sqs/client/send_message"


def test_one_per_page_collapses_duplicate_pages(store) -> None:
    many = Retriever(store, mode="vector", one_per_page=False, service_filter=False)
    one = Retriever(store, mode="vector", one_per_page=True, service_filter=False)

    pages_many = [c.page for c in many.search("upload a file bucket", top_k=6)]
    pages_one = [c.page for c in one.search("upload a file bucket", top_k=6)]

    assert pages_many.count("s3/client/upload_file") == 2
    assert pages_one.count("s3/client/upload_file") == 1
    assert len(pages_one) == len(set(pages_one))


def test_service_filter_keeps_named_service_only(store) -> None:
    filtered = Retriever(store, mode="hybrid", service_filter=True)
    services = {c.metadata["service"] for c in filtered.search("stop an EC2 instance", top_k=5)}
    assert services == {"ec2"}


def test_service_filter_off_when_two_services_named(store) -> None:
    r = Retriever(store, mode="hybrid", service_filter=True)
    services = {c.metadata["service"] for c in r.search("stop EC2 or RDS instance", top_k=5)}
    assert {"ec2", "rds"} <= services


def test_explicit_where_overrides_service_detection(store) -> None:
    r = Retriever(store, mode="bm25", service_filter=True)
    results = r.search("stop an EC2 instance", top_k=5, where={"service": "rds"})
    assert {c.metadata["service"] for c in results} == {"rds"}


def test_top_k_is_respected(store) -> None:
    assert len(Retriever(store, service_filter=False).search("bucket", top_k=2)) <= 2


# ------------------------------------------------------------------ evaluation

def test_first_hit_rank() -> None:
    assert first_hit_rank(["a", "b", "c"], ["c", "x"]) == 3
    assert first_hit_rank(["a", "b"], ["z"]) is None


def test_evaluate_metrics(store) -> None:
    questions = [
        Question(1, "send a message to a queue", ["sqs/client/send_message"]),
        Question(2, "something nobody wrote about", ["lambda/client/invoke"]),
    ]
    result = evaluate(Retriever(store, service_filter=False), questions, k=5)

    assert result.n == 2
    assert result.recall_at_k == 0.5
    assert result.recall_at_1 == 0.5
    assert result.mrr == 0.5
    assert [m["id"] for m in result.misses] == [2]


def test_load_questions_filters_split(tmp_path) -> None:
    path = tmp_path / "q.json"
    path.write_text(json.dumps([
        {"id": 1, "q": "a", "pages": ["x"], "split": "dev"},
        {"id": 2, "q": "b", "pages": ["y"], "split": "test"},
    ]), encoding="utf-8")

    assert [q.id for q in load_questions(str(path), "dev")] == [1]
    assert [q.id for q in load_questions(str(path), "test")] == [2]
    assert len(load_questions(str(path), "all")) == 2


def test_real_question_file_is_well_formed() -> None:
    questions = load_questions("eval/retrieval_questions.json", "all")
    assert len(questions) >= 50
    assert len({q.id for q in questions}) == len(questions)
    assert {q.split for q in questions} == {"dev", "test"}
    assert all(q.pages and all("/" in p for p in q.pages) for q in questions)
