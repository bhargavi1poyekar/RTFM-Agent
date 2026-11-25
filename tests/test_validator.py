"""
Tests for ChunkValidator: each quality rule, the report, and filter_valid().
"""
from typing import List

import pytest

from config.settings import settings
from src.rtfm_agent.ingestion.base import DocumentChunk
from src.rtfm_agent.ingestion.validator import ChunkValidator, ValidationReport

GOOD_TEXT = "The put_object method uploads a file to an S3 bucket. " * 5  # ~270 chars


def make_chunk(content: str = GOOD_TEXT, **overrides) -> DocumentChunk:
    fields = dict(
        chunk_id="id",
        content=content,
        source_url="https://example.com/s3.html",
        title="S3",
        section="Client",
        metadata={},
        code_blocks=[],
        created_at="2026-01-01T00:00:00",
    )
    fields.update(overrides)
    return DocumentChunk(**fields)


def issues_for(chunk: DocumentChunk, validator: ChunkValidator = None) -> List[str]:
    validator = validator or ChunkValidator()
    return validator._validate_chunk(chunk, set())


def test_defaults_match_chunker_settings() -> None:
    v = ChunkValidator()
    assert v.min_length == settings.CHUNK_MIN_SIZE
    assert v.max_length == settings.CHUNK_SIZE


def test_good_chunk_has_no_issues() -> None:
    assert issues_for(make_chunk()) == []


@pytest.mark.parametrize(
    "chunk, expected",
    [
        (make_chunk(content=""), "empty_content"),
        (make_chunk(content="short"), "too_short"),
        (make_chunk(content="x" * (settings.CHUNK_SIZE + 1)), "too_long"),
        (make_chunk(source_url=""), "missing_source_url"),
        (make_chunk(title=""), "missing_title"),
        (make_chunk(section=""), "missing_section"),
        (make_chunk(content=" " * 300), "whitespace_only"),
        (make_chunk(content="# " + "Heading " * 40), "header_only"),
        (make_chunk(content="http://a.co " * 30), "navigation_heavy"),
        (make_chunk(code_blocks=["x = 1"]), "tiny_code_block"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_each_rule_detects_its_issue(chunk: DocumentChunk, expected: str) -> None:
    assert expected in issues_for(chunk)


def test_duplicate_detected_once_not_twice() -> None:
    v = ChunkValidator()
    report = v.validate([make_chunk(), make_chunk()])

    assert report.issues == {"duplicate": 1}
    assert report.valid_chunks == 1


def test_whitespace_only_difference_is_duplicate() -> None:
    a = make_chunk(content=GOOD_TEXT)
    b = make_chunk(content="  " + GOOD_TEXT.replace(" ", "\n", 3) + "\n")

    assert ChunkValidator().validate([a, b]).issues == {"duplicate": 1}


def test_shared_prefix_with_different_content_is_not_duplicate() -> None:
    """Regression: e.g. S3 and EC2 'Waiters' sections share their opening
    sentence but list different waiters; both must be kept."""
    a = make_chunk(content=GOOD_TEXT + " Waiters: BucketExists, ObjectExists.")
    b = make_chunk(content=GOOD_TEXT + " Waiters: InstanceRunning, VolumeAvailable.")

    report = ChunkValidator().validate([a, b])

    assert report.valid_chunks == 2


def test_report_counts_and_pass_rate() -> None:
    chunks = [make_chunk(), make_chunk(content="short"), make_chunk(content=GOOD_TEXT[::-1])]

    report = ChunkValidator().validate(chunks)

    assert (report.total_chunks, report.valid_chunks, report.invalid_chunks) == (3, 2, 1)
    assert report.pass_rate == pytest.approx(66.67, abs=0.01)
    assert report.stats["unique_sources"] == 1
    assert "VALIDATION REPORT" in str(report)


def test_empty_input() -> None:
    report = ChunkValidator().validate([])

    assert report.pass_rate == 0.0
    assert report.stats == {}


def test_filter_valid_agrees_with_validate() -> None:
    chunks = [
        make_chunk(),
        make_chunk(),  # duplicate
        make_chunk(content="short"),
        make_chunk(content=GOOD_TEXT + " different tail"),  # shared prefix only
        make_chunk(content=GOOD_TEXT[::-1]),
    ]
    v = ChunkValidator()

    assert len(v.filter_valid(chunks)) == v.validate(chunks).valid_chunks == 3


def test_pass_rate_property_handles_zero() -> None:
    assert ValidationReport(0, 0, 0, {}, {}).pass_rate == 0.0
