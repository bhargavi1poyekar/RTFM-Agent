"""
Offline tests for the Boto3 scraper and the batch_scrape script.

No real HTTP: a fake session returns hand-written HTML, so tests are fast,
deterministic and don't hit AWS servers.
"""
import importlib.util
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock

import pytest

from config.settings import settings
from src.rtfm_agent.ingestion.base import BaseScraper
from src.rtfm_agent.ingestion.boto3_scraper import Boto3Scraper

BASE = "https://boto3.amazonaws.com/v1/documentation/api/latest"
PAGE_A = f"{BASE}/reference/services/sqs.html"
PAGE_B = f"{BASE}/reference/services/sqs/client/send_message.html"

# Link lives inside <nav>; get_page_content() removes <nav>, so this also
# checks get_links() still sees links after content extraction.
HTML_A = f"""
<html><head><title>SQS - Boto3 docs</title></head><body>
<div class="body">
  <nav><a href="{PAGE_B}">send_message</a></nav>
  <h2>Client</h2>
  <p>{"The SQS client lets you send and receive queue messages. " * 15}</p>
  <div class="highlight-python"><div class="highlight"><pre>import boto3
client = boto3.client("sqs")</pre></div></div>
</div></body></html>
""".encode()

HTML_B = b"""
<html><head><title>send_message</title></head><body>
<div class="body"><p>Delivers a message to the specified queue.</p></div>
</body></html>
"""


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self) -> None:
        pass


class FakeSession:
    """Stands in for requests.Session and records every URL requested."""

    def __init__(self, pages: Dict[str, bytes]):
        self.pages = pages
        self.calls: List[str] = []
        self.headers: Dict[str, str] = {}

    def get(self, url: str, timeout: int = 30) -> FakeResponse:
        self.calls.append(url)
        return FakeResponse(self.pages[url])


@pytest.fixture
def scraper(tmp_path: Path) -> Boto3Scraper:
    s = Boto3Scraper(output_dir=str(tmp_path), requests_per_second=1000)
    s.session = FakeSession({PAGE_A: HTML_A, PAGE_B: HTML_B})
    return s


def test_each_page_is_downloaded_once(scraper: Boto3Scraper) -> None:
    scraper.scrape(max_pages=2, seed_urls=[PAGE_A])

    assert scraper.session.calls == [PAGE_A, PAGE_B]


def test_links_found_even_though_nav_is_stripped(scraper: Boto3Scraper) -> None:
    scraper.get_page_content(PAGE_A)  # strips <nav> from its own parsed copy

    assert PAGE_B in scraper.get_links(PAGE_A)


def test_clean_content_keeps_code_block(scraper: Boto3Scraper) -> None:
    page = scraper.get_page_content(PAGE_A)

    assert '```python\nimport boto3\nclient = boto3.client("sqs")\n```' in page["content"]
    assert "__CODEBLOCK_" not in page["content"]


def test_chunks_respect_max_size(scraper: Boto3Scraper) -> None:
    sections = [f"## Section {i}\n\n" + ("word " * 600) for i in range(5)]
    chunks = scraper.chunk_content("\n\n".join(sections), {"url": PAGE_A, "title": "t"})

    assert chunks
    assert all(len(c.content) <= settings.CHUNK_SIZE for c in chunks)


def test_base_scraper_default_prioritize_keeps_order() -> None:
    class Minimal(BaseScraper):
        def get_page_content(self, url): return {}
        def get_links(self, url): return []
        def clean_content(self, html_content): return ""
        def extract_code_blocks(self, html_content): return []
        def chunk_content(self, content, metadata): return []

    s = Minimal(base_url="https://example.com", output_dir="data/raw")
    assert s.prioritize_urls(["b", "a"]) == ["b", "a"]


def test_batch_scrape_passes_seed_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "batch_scrape.py"
    spec = importlib.util.spec_from_file_location("batch_scrape", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fake = MagicMock()
    fake.get_all_seeds.return_value = ["seed1", "seed2"]
    fake.scrape.return_value = []
    monkeypatch.setattr(module, "Boto3Scraper", lambda **kwargs: fake)

    module.batch_scrape(max_pages=3, validate=False)

    fake.scrape.assert_called_once_with(max_pages=3, seed_urls=["seed1", "seed2"])


# --- Crawl order / coverage -------------------------------------------------

SVC = f"{BASE}/reference/services"


def test_focus_services_derived_from_seeds(scraper: Boto3Scraper) -> None:
    assert {"s3", "ec2", "iam", "lambda"} <= scraper.focus_services


def test_focus_service_pages_before_other_services(scraper: Boto3Scraper) -> None:
    urls = [
        f"{SVC}/waf.html",                          # other service landing
        f"{SVC}/s3/paginator/ListObjectsV2.html",   # focus paginator
        f"{BASE}/guide/quickstart.html",            # guide
        f"{SVC}/s3/client/put_object.html",         # focus client method
        f"{SVC}/s3.html",                           # focus landing
    ]

    assert scraper.prioritize_urls(urls) == [
        f"{SVC}/s3.html",
        f"{SVC}/s3/client/put_object.html",
        f"{SVC}/s3/paginator/ListObjectsV2.html",
        f"{BASE}/guide/quickstart.html",
        f"{SVC}/waf.html",
    ]


def test_round_robin_across_focus_services(scraper: Boto3Scraper) -> None:
    ec2 = [f"{SVC}/ec2/client/m{i}.html" for i in range(5)]
    s3 = [f"{SVC}/s3/client/m{i}.html" for i in range(2)]

    ordered = scraper.prioritize_urls(ec2 + s3)  # EC2 discovered first

    # S3 gets a turn right away instead of waiting behind all of EC2
    assert ordered[:4] == [ec2[0], s3[0], ec2[1], s3[1]]


def test_links_after_sidebar_are_not_cut_off(scraper: Boto3Scraper) -> None:
    """Regression: get_links() kept only the first 100 links, which on real
    service pages are all sidebar links, so method pages were never found."""
    sidebar = "".join(f'<a href="{SVC}/svc{i}.html">s</a>' for i in range(600))
    own = f'<a href="{SVC}/s3/client/put_object.html">put_object</a>'
    url = f"{SVC}/s3.html"
    scraper.session = FakeSession({url: f"<html><body>{sidebar}{own}</body></html>".encode()})

    assert f"{SVC}/s3/client/put_object.html" in scraper.get_links(url)
