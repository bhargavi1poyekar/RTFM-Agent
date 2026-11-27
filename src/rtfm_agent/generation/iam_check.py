# src/rtfm_agent/generation/iam_check.py
"""
Checks every action in a generated IAM policy against the docs.

A made-up action in a confident-looking policy is much harder to spot than
broken Python, so each action gets one of these verdicts:

- grounded:   supported by a page we retrieved for this question, either
              because the action is named there ("s3:GetObject") or the
              page is that operation ("ec2:RunInstances" <->
              ec2/client/run_instances; "s3:PutObject" <-> the upload_file
              helper, see BOTO3_HELPERS)
- in_docs:    a real operation/action somewhere in the docs, but not in
              what we retrieved (so the answer isn't citing evidence for it)
- not_found:  no operation or mention anywhere in the docs: likely invented
- too_broad:  a wildcard such as "*" or "s3:*"

Limits: IAM action names usually match API names (PutObject <-> put_object),
but not always. s3:ListBucket is the permission for list_objects_v2, for
example. Those are caught only when the docs mention the action by name,
so treat not_found as "check this", not as proof it's wrong.
"""
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set

_ACTION = re.compile(r"\b([a-z0-9-]+):([A-Za-z0-9*]+)\b")

# Boto3-only helpers: real methods with their own docs pages, but not AWS
# API calls, so there's no IAM action with their name. Each maps to the
# actions it actually needs (upload_file calls PutObject under the hood).
BOTO3_HELPERS: Dict[str, List[str]] = {
    "upload_file": ["PutObject"],
    "upload_fileobj": ["PutObject"],
    "download_file": ["GetObject"],
    "download_fileobj": ["GetObject"],
    "copy": ["GetObject", "PutObject"],
    "generate_presigned_url": [],
    "generate_presigned_post": [],
    "get_paginator": [],
    "get_waiter": [],
    "can_paginate": [],
    "close": [],
}

# Page types whose page name is an operation (client.run_instances,
# paginator ListObjectsV2, resource methods like queue.send_message).
_NOT_OPERATIONS = {"landing", "other", "waiter"}


def norm(name: str) -> str:
    """'DescribeDBInstances' and 'describe_db_instances' -> 'describedbinstances'."""
    return name.replace("_", "").lower()


def page_actions(meta: Dict) -> Set[str]:
    """IAM action keys a docs page is evidence for, from its URL metadata."""
    service, page = meta.get("service", ""), meta.get("page", "")
    if not service or meta.get("page_type") in _NOT_OPERATIONS:
        return set()
    names = BOTO3_HELPERS.get(page, [page])
    return {f"{service}:{norm(n)}" for n in names}


def policy_actions(policy: Dict) -> List[str]:
    """All Action / NotAction strings in a policy, in order, de-duplicated."""
    statements = policy.get("Statement", []) if isinstance(policy, dict) else []
    if isinstance(statements, dict):
        statements = [statements]
    actions: List[str] = []
    for st in statements:
        if not isinstance(st, dict):
            continue
        for key in ("Action", "NotAction"):
            value = st.get(key, [])
            actions.extend([value] if isinstance(value, str) else value)
    return list(dict.fromkeys(a for a in actions if isinstance(a, str)))


def mentioned_actions(text: str) -> Set[str]:
    """Normalised 'service:action' strings written out in a piece of text."""
    return {f"{svc}:{norm(act)}" for svc, act in _ACTION.findall(text)}


@dataclass
class ActionCheck:
    action: str
    verdict: str              # grounded | in_docs | not_found | too_broad
    evidence: Optional[str]   # page that supports it, if any


class DocsCatalog:
    """Every client operation and every written-out IAM action in the
    library, as normalised 'service:action' keys."""

    def __init__(self, operations: Iterable[str], mentions: Iterable[str]):
        self.known = set(operations) | set(mentions)

    @classmethod
    def from_metadata(cls, metadatas: Iterable[Dict], documents: Iterable[str] = ()) -> "DocsCatalog":
        ops = set().union(*(page_actions(m) for m in metadatas))
        mentions = set().union(*(mentioned_actions(d) for d in documents))
        return cls(ops, mentions)

    @classmethod
    def from_store(cls, store) -> "DocsCatalog":
        data = store.collection.get(include=["documents", "metadatas"])
        return cls.from_metadata(data["metadatas"], data["documents"])


def check_policy(policy: Dict, chunks: List, catalog: Optional[DocsCatalog] = None) -> List[ActionCheck]:
    """Verdict for each action in `policy`, given the retrieved `chunks`."""
    # What the retrieved pages support: their own operation + actions they name
    support: Dict[str, str] = {}
    for c in chunks:
        meta = c.metadata
        page = meta.get("source_url", "")
        for key in page_actions(meta):
            support.setdefault(key, page)
        for key in mentioned_actions(c.text):
            support.setdefault(key, page)

    results = []
    for action in policy_actions(policy):
        if "*" in action:
            results.append(ActionCheck(action, "too_broad", None))
            continue
        # IAM ignores case, but not underscores: "ec2:describe_instances"
        # is not a valid action, so only page names get underscores removed.
        svc, _, name = action.partition(":")
        key = f"{svc.lower()}:{name.lower()}"
        if key in support:
            results.append(ActionCheck(action, "grounded", support[key]))
        elif catalog is not None and key in catalog.known:
            results.append(ActionCheck(action, "in_docs", None))
        else:
            results.append(ActionCheck(action, "not_found", None))
    return results
