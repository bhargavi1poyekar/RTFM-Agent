"""
Scrape coverage: every focus service should have (almost) all of its
operations scraped. The data test runs only where data/raw/pages.json
exists (it's gitignored); the logic tests always run.
"""
import json
import os

import pytest

from src.rtfm_agent.ingestion.boto3_scraper import Boto3Scraper
from src.rtfm_agent.ingestion.coverage import MIN_COVERAGE, botocore_operations, coverage

BASE = "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services"
PAGES_FILE = "data/raw/pages.json"


def fake_ops(service):
    return {"ec2": ["run_instances", "stop_instances", "describe_vpcs"],
            "sqs": ["send_message"]}[service]


def test_coverage_counts_missing_operations() -> None:
    urls = [f"{BASE}/ec2/client/describe_vpcs.html", f"{BASE}/ec2/client/run_instances.html#x",
            f"{BASE}/sqs/queue/send_message.html"]  # resource page, not the client page
    report = coverage(urls, ["ec2", "sqs"], operations=fake_ops)

    assert report["ec2"].missing == ["stop_instances"]
    assert report["ec2"].ratio == pytest.approx(2 / 3)
    assert report["sqs"].found == 0


def test_botocore_knows_the_operations_we_rely_on() -> None:
    ops = botocore_operations("ec2")
    assert {"run_instances", "stop_instances", "terminate_instances"} <= set(ops)
    assert len(ops) > 700


@pytest.mark.skipif(not os.path.exists(PAGES_FILE), reason="no scraped data")
def test_scrape_covers_every_focus_service() -> None:
    with open(PAGES_FILE, encoding="utf-8") as f:
        urls = [p["url"] for p in json.load(f)]
    services = Boto3Scraper().focus_services
    report = coverage(urls, services)

    short = {s: f"{c.found}/{c.total} (missing e.g. {c.missing[:3]})"
             for s, c in report.items() if c.ratio < MIN_COVERAGE}
    assert not short, f"Scrape is missing operations: {short}"
