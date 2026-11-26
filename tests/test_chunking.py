"""
Tests for Boto3Scraper.chunk_content() and its splitting helpers.

Pure string-in / chunks-out tests: no network, no files.
"""
import pytest

from config.settings import settings
from src.rtfm_agent.ingestion.boto3_scraper import Boto3Scraper

META = {"url": "https://example.com/page.html", "title": "Page"}


@pytest.fixture
def scraper(tmp_path) -> Boto3Scraper:
    return Boto3Scraper(output_dir=str(tmp_path))


def sizes(chunks, scraper: Boto3Scraper):
    """Chunk sizes in the scraper's own unit (tokens by default)."""
    return [scraper._size(c.content) for c in chunks]


def test_defaults_come_from_settings(scraper: Boto3Scraper) -> None:
    assert scraper.max_chunk_size == settings.CHUNK_MAX_TOKENS
    assert scraper.min_chunk_size == settings.CHUNK_MIN_TOKENS


def test_empty_or_tiny_content_gives_no_chunks(scraper: Boto3Scraper) -> None:
    assert scraper.chunk_content("", META) == []
    assert scraper.chunk_content("too short", META) == []


def test_small_sections_are_merged(scraper: Boto3Scraper) -> None:
    text = "## A\n\n" + "alpha " * 30 + "\n\n## B\n\n" + "beta " * 30

    chunks = scraper.chunk_content(text, META)

    assert len(chunks) == 1
    assert "alpha" in chunks[0].content and "beta" in chunks[0].content
    assert "## B" in chunks[0].content  # merged section keeps its heading


def test_heading_does_not_push_chunk_over_max(tmp_path) -> None:
    """Regression: the '## heading' used to be added after the size check."""
    s = Boto3Scraper(output_dir=str(tmp_path), min_chunk_size=10, max_chunk_size=300, length_fn=len)
    long_header = "H" * 60
    first = "## First\n\n" + "x " * 100
    # Section body alone fits in 300, but body + "## <long header>\n\n" does not
    second = f"## {long_header}\n\n" + "y " * 140

    chunks = s.chunk_content(first + "\n\n" + second, META)

    assert chunks
    assert max(sizes(chunks, s)) <= 300


@pytest.mark.parametrize(
    "text",
    [
        # Giant paragraph right after a code block (used to recurse forever)
        "## Intro\n\n```python\nimport boto3\n```\n\n" + "word " * 2000,
        # "\n##x" is not a heading but used to trigger header splitting forever
        "## Intro\n\n" + ("token " * 300 + "\n##notaheading ") * 5,
        # One huge line with no spaces, newlines or sentences -> hard split.
        # (Not "a" * 5000: the tokenizer maps any 100+ char word to a single
        # [UNK] token, so that would be tiny when measured in tokens.)
        "## Intro\n\n" + "ab," * 2000,
    ],
    ids=["giant-paragraph", "fake-heading", "no-boundaries"],
)
def test_oversized_text_terminates_and_respects_max(
    scraper: Boto3Scraper, text: str, tmp_path
) -> None:
    # Must terminate whether size is measured in tokens or characters
    for s in (scraper, Boto3Scraper(output_dir=str(tmp_path), length_fn=len,
                                    min_chunk_size=150, max_chunk_size=700)):
        chunks = s.chunk_content(text, META)

        assert chunks
        assert max(sizes(chunks, s)) <= s.max_chunk_size


def test_no_text_lost_when_splitting_paragraphs(scraper: Boto3Scraper) -> None:
    words = [f"w{i}" for i in range(600)]
    paragraphs = [" ".join(words[i:i + 20]) for i in range(0, 600, 20)]
    text = "## Long\n\n" + "\n\n".join(paragraphs)

    chunks = scraper.chunk_content(text, META)
    joined = " ".join(c.content for c in chunks)

    assert all(w in joined.split() for w in words)


def test_chunks_below_min_size_are_dropped(tmp_path) -> None:
    s = Boto3Scraper(output_dir=str(tmp_path), min_chunk_size=200, max_chunk_size=700, length_fn=len)
    text = "## Tiny\n\n" + "z " * 40  # ~90 chars

    assert s.chunk_content(text + "\n" * 5 + "padding " * 3, META) == []


def test_code_blocks_recorded_on_chunk(scraper: Boto3Scraper) -> None:
    code = "```python\nimport boto3\ns3 = boto3.client('s3')\n```"
    text = "## Usage\n\nCreate a client like this:\n\n" + code + "\n\n" + "explain " * 20

    chunks = scraper.chunk_content(text, META)

    assert len(chunks) == 1
    assert len(chunks[0].code_blocks) == 1
    assert "boto3.client('s3')" in chunks[0].code_blocks[0]


def test_duplicate_chunks_within_page_removed(tmp_path) -> None:
    s = Boto3Scraper(output_dir=str(tmp_path), min_chunk_size=50, max_chunk_size=500, length_fn=len)
    body = "same content repeated " * 20
    text = f"## One\n\n{body}\n\n## Two\n\n" + "different " * 70 + f"\n\n## One\n\n{body}"

    contents = [c.content for c in s.chunk_content(text, META)]

    # "## One" appears twice in the page but is kept once
    assert sum(c.startswith("same content") for c in contents) == 1
    assert any(c.startswith("different") for c in contents)


def test_shared_prefix_chunks_both_kept(tmp_path) -> None:
    """Regression: dedup used a 100-char prefix and dropped different content."""
    s = Boto3Scraper(output_dir=str(tmp_path), min_chunk_size=50, max_chunk_size=400, length_fn=len)
    intro = "Waiters are available on a client instance via the get_waiter method. " * 2
    text = f"## A\n\n{intro}BucketExists\n\n" + "x " * 200 + f"\n\n## B\n\n{intro}InstanceRunning"

    joined = " ".join(c.content for c in s.chunk_content(text, META))

    assert "BucketExists" in joined and "InstanceRunning" in joined


def test_chunk_ids_are_deterministic(scraper: Boto3Scraper) -> None:
    text = "## A\n\n" + "stable text " * 30

    first = [c.chunk_id for c in scraper.chunk_content(text, META)]
    second = [c.chunk_id for c in scraper.chunk_content(text, META)]

    assert first == second


def test_chunk_carries_source_metadata(scraper: Boto3Scraper) -> None:
    chunk = scraper.chunk_content("## A\n\n" + "text " * 50, META)[0]

    assert chunk.source_url == META["url"]
    assert chunk.title == META["title"]
    assert chunk.section == "A"
