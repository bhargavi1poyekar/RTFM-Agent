# src/rtfm_agent/ingestion/coverage.py
"""
Did the scrape actually get every operation of the services we care about?

The expected list comes from botocore's own service models (offline, no AWS
account needed), e.g. EC2 -> 830 operations -> run_instances, stop_instances,
... Each is expected at .../reference/services/<service>/client/<op>.html.

Added after the 2,300-page scrape quietly stopped EC2 at
describe_vpn_concentrators (486 of 834 operations) and nothing complained.
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional

from src.rtfm_agent.retrieval.retriever import page_key

MIN_COVERAGE = 0.95


@dataclass
class ServiceCoverage:
    service: str
    total: int
    missing: List[str] = field(default_factory=list)

    @property
    def found(self) -> int:
        return self.total - len(self.missing)

    @property
    def ratio(self) -> float:
        return self.found / self.total if self.total else 1.0


def botocore_operations(service: str) -> List[str]:
    """Snake-case operation names for a service, e.g. ['run_instances', ...]."""
    import boto3
    from botocore import xform_name

    client = boto3.session.Session(region_name="us-east-1").client(service)
    return [xform_name(op) for op in client.meta.service_model.operation_names]


def coverage(
    urls: Iterable[str],
    services: Iterable[str],
    operations: Optional[Callable[[str], List[str]]] = None,
) -> Dict[str, ServiceCoverage]:
    """Per service: how many of its operations have a scraped client page."""
    operations = operations or botocore_operations
    pages = {page_key(u) for u in urls}
    report = {}
    for svc in sorted(services):
        ops = operations(svc)
        report[svc] = ServiceCoverage(svc, len(ops),
                                      [op for op in ops if f"{svc}/client/{op}" not in pages])
    return report
