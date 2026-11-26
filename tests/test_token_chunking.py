"""
Token-based chunk sizing: every chunk, plus the breadcrumb it is embedded
with, must fit in what all-MiniLM-L6-v2 actually reads (256 tokens
including [CLS] and [SEP]). Also covers re-chunking from saved pages.
"""
import json

import pytest

from config.settings import settings
from src.rtfm_agent.ingestion.boto3_scraper import Boto3Scraper
from src.rtfm_agent.ingestion.tokens import count_tokens

MODEL_LIMIT = 256
SPECIAL_TOKENS = 2  # [CLS] + [SEP]
# Longest breadcrumb in the real data (23 tokens)
LONGEST_HEADER = (
    "ec2 > client > describe_local_gateway_route_table_virtual_interface_group_associations"
)
META = {"url": "https://example.com/page.html", "title": "Page"}


@pytest.fixture
def scraper(tmp_path) -> Boto3Scraper:
    return Boto3Scraper(output_dir=str(tmp_path))


def test_count_tokens_excludes_special_tokens() -> None:
    assert count_tokens("hi") == 1
    assert count_tokens("") == 0


def test_budget_leaves_room_for_breadcrumb_and_special_tokens() -> None:
    header = count_tokens(LONGEST_HEADER + "\n\n")
    assert settings.CHUNK_MAX_TOKENS + header + SPECIAL_TOKENS <= MODEL_LIMIT


@pytest.mark.parametrize(
    "body",
    [
        # Code-dense: ~1.3 chars/token. 700 chars of this was ~520 tokens.
        "'m5n.2xlarge'|'m5zn.large'|'m6a.xlarge'|" * 120,
        # Python call with many short keyword args
        "```python\nresponse = client.put_bucket_lifecycle_configuration(\n"
        + "".join(f"    Arg{i}={{'ID': 'r{i}', 'Days': {i}}},\n" for i in range(150))
        + ")\n```",
        # Ordinary prose
        "This operation uploads an object to the bucket you specify. " * 80,
    ],
    ids=["instance-type-list", "python-call", "prose"],
)
def test_every_chunk_fits_the_model_with_its_breadcrumb(
    scraper: Boto3Scraper, body: str
) -> None:
    chunks = scraper.chunk_content("## Request Syntax\n\n" + body, META)

    assert chunks
    for chunk in chunks:
        embedded = f"{LONGEST_HEADER}\n\n{chunk.content}"
        assert count_tokens(embedded) + SPECIAL_TOKENS <= MODEL_LIMIT


def test_dense_text_gets_more_chunks_than_prose_of_same_length(scraper) -> None:
    dense = "'m5n.2xlarge'|" * 300
    prose = ("word " * 1000)[: len(dense)]

    n_dense = len(scraper.chunk_content("## A\n\n" + dense, META))
    n_prose = len(scraper.chunk_content("## A\n\n" + prose, META))

    assert n_dense > n_prose


def test_hard_split_keeps_all_text_and_respects_limit(tmp_path) -> None:
    s = Boto3Scraper(output_dir=str(tmp_path), length_fn=len)
    text = "abc def " * 100

    pieces = s._hard_split(text, 50)

    assert all(len(p) <= 50 for p in pieces)
    assert "".join(pieces) == text


def test_hard_split_by_tokens(scraper: Boto3Scraper) -> None:
    text = "'m5n.2xlarge'|" * 200
    pieces = scraper._hard_split(text, 40)

    assert all(count_tokens(p) <= 40 for p in pieces)
    assert "".join(pieces) == text


def test_rechunk_pages_reads_saved_pages_without_network(tmp_path) -> None:
    pages = [{
        "url": "https://example.com/s3/client/put_object.html",
        "title": "put_object",
        "content": "## Overview\n\n" + "Uploads an object to a bucket. " * 60,
        "scraped_at": "2026-11-26T00:00:00",
    }]
    (tmp_path / "pages.json").write_text(json.dumps(pages), encoding="utf-8")
    s = Boto3Scraper(output_dir=str(tmp_path))
    s.session = None  # any network use would raise

    chunks = s.rechunk_pages()

    assert chunks
    assert all(c.source_url == pages[0]["url"] for c in chunks)
    saved = json.loads((tmp_path / "chunks.json").read_text(encoding="utf-8"))
    assert len(saved) == len(chunks)
