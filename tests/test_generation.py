"""
Tests for step 4: prompt building, answer parsing, the IAM grounding check,
the agent pipeline and POST /api/v1/query. A FakeLLM returns canned replies,
so no model or network is needed.
"""
import json

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from src.rtfm_agent.api import query as query_api
from src.rtfm_agent.embedding.vector_store import VectorStore
from src.rtfm_agent.generation.agent import RTFMAgent
from src.rtfm_agent.generation.iam_check import (
    DocsCatalog,
    check_policy,
    mentioned_actions,
    norm,
    policy_actions,
)
from src.rtfm_agent.generation.llm import LLMError, get_llm
from src.rtfm_agent.generation.prompt import NOT_FOUND, build_prompt, parse_answer
from src.rtfm_agent.ingestion.base import DocumentChunk, make_chunk_id
from src.rtfm_agent.main import app
from src.rtfm_agent.retrieval.retriever import RetrievedChunk, Retriever
from tests.fakes import FakeEmbedder

BASE = "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services"


def retrieved(page: str, text: str) -> RetrievedChunk:
    service, page_type, name = page.split("/")
    return RetrievedChunk(
        chunk_id=page, text=text, score=1.0,
        metadata={"source_url": f"{BASE}/{page}.html", "title": name,
                  "service": service, "page_type": page_type, "page": name},
    )


def reply(policy_actions_: list, prose: str = "Use put_object [1].") -> str:
    policy = {"Version": "2012-10-17",
              "Statement": [{"Effect": "Allow", "Action": policy_actions_,
                             "Resource": "arn:aws:s3:::my-bucket/*"}]}
    return (f"{prose}\n\n```python\nimport boto3\ns3 = boto3.client('s3')\n"
            f"first = s3.list_objects_v2(Bucket='b')['Contents'][0]['Key']\n```\n\n"
            f"```json\n{json.dumps(policy)}\n```")


class FakeLLM:
    name = "fake/llm"

    def __init__(self, response: str = ""):
        self.response = response
        self.calls = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


# ---------------------------------------------------------------- prompt/parse

def test_prompt_numbers_sources_and_includes_urls() -> None:
    chunks = [retrieved("s3/client/put_object", "s3 > client > put_object\n\nAdds an object."),
              retrieved("s3/client/get_object", "s3 > client > get_object\n\nRetrieves.")]
    system, user = build_prompt("Upload a file?", chunks)

    assert NOT_FOUND in system
    assert f"[1] {BASE}/s3/client/put_object.html" in user
    assert f"[2] {BASE}/s3/client/get_object.html" in user
    assert user.rstrip().endswith("Question: Upload a file?")


def test_parse_extracts_code_policy_and_citations() -> None:
    parsed = parse_answer(reply(["s3:PutObject"], "Call put_object [2], see [1][2]."), n_sources=3)

    assert parsed.found
    assert parsed.code.startswith("import boto3")
    assert policy_actions(parsed.policy) == ["s3:PutObject"]
    assert parsed.cited == [2, 1]
    assert parsed.warnings == []


def test_indexing_inside_code_is_not_a_citation() -> None:
    # reply()'s code does ['Contents'][0]; only prose citations count
    parsed = parse_answer(reply(["s3:PutObject"], "See [1]."), n_sources=1)
    assert parsed.cited == [1]
    assert parsed.warnings == []


def test_parse_flags_made_up_citation_and_missing_parts() -> None:
    parsed = parse_answer("Just call it [7].", n_sources=5)

    assert parsed.code is None and parsed.policy is None
    assert any("don't exist: [7]" in w for w in parsed.warnings)
    assert any("No Python code" in w for w in parsed.warnings)
    assert any("No IAM policy" in w for w in parsed.warnings)


def test_parse_flags_invalid_policy_json() -> None:
    parsed = parse_answer("See [1].\n```json\n{not json}\n```", n_sources=1)
    assert parsed.policy is None
    assert any("not valid JSON" in w for w in parsed.warnings)


def test_parse_recognises_not_found() -> None:
    parsed = parse_answer(NOT_FOUND, n_sources=5)
    assert not parsed.found and parsed.warnings == []


# ------------------------------------------------------------------ IAM check

def test_norm_matches_iam_and_boto3_names() -> None:
    assert norm("DescribeDBInstances") == norm("describe_db_instances")
    assert norm("ListObjectsV2") == norm("list_objects_v2")


def test_policy_actions_handles_strings_lists_and_single_statement() -> None:
    policy = {"Statement": {"Action": "s3:GetObject"}}
    assert policy_actions(policy) == ["s3:GetObject"]
    policy = {"Statement": [{"Action": ["a:X", "a:Y"]}, {"NotAction": "a:X"}]}
    assert policy_actions(policy) == ["a:X", "a:Y"]
    assert policy_actions({"no": "statement"}) == []


def test_mentioned_actions_finds_written_out_actions() -> None:
    assert "s3:listbucket" in mentioned_actions("you need the s3:ListBucket permission")


def test_check_policy_verdicts() -> None:
    chunks = [
        retrieved("ec2/client/run_instances", "Launches instances."),
        retrieved("s3/client/get_object", "You need the s3:ListBucket permission."),
    ]
    catalog = DocsCatalog(operations=["iam:passrole", "ec2:runinstances"], mentions=[])
    policy = {"Statement": [{"Action": [
        "ec2:RunInstances",      # the retrieved page *is* this operation
        "s3:GetObject",          # same
        "s3:ListBucket",         # named in a retrieved page
        "iam:PassRole",          # real, but not retrieved
        "ec2:LaunchVirtualBox",  # invented
        "s3:*",                  # wildcard
    ]}]}

    verdicts = {c.action: c.verdict for c in check_policy(policy, chunks, catalog)}

    assert verdicts == {
        "ec2:RunInstances": "grounded",
        "s3:GetObject": "grounded",
        "s3:ListBucket": "grounded",
        "iam:PassRole": "in_docs",
        "ec2:LaunchVirtualBox": "not_found",
        "s3:*": "too_broad",
    }


def test_boto3_helpers_map_to_real_actions_not_their_own_name() -> None:
    # upload_file is a Boto3 helper: it needs s3:PutObject, and there is no
    # "s3:UploadFile" IAM action, even though it has its own docs page.
    chunks = [retrieved("s3/bucket/upload_file", "Upload a file to an S3 object."),
              retrieved("sqs/queue/send_message", "Delivers a message.")]
    catalog = DocsCatalog.from_metadata([{"service": "s3", "page_type": "client", "page": "upload_file"}])
    policy = {"Statement": [{"Action": ["s3:PutObject", "s3:UploadFile", "sqs:SendMessage"]}]}

    verdicts = {c.action: c.verdict for c in check_policy(policy, chunks, catalog)}

    assert verdicts == {"s3:PutObject": "grounded", "s3:UploadFile": "not_found",
                        "sqs:SendMessage": "grounded"}


def test_grounded_evidence_points_to_the_page() -> None:
    chunks = [retrieved("ec2/client/run_instances", "Launches instances.")]
    [check] = check_policy({"Statement": [{"Action": "ec2:RunInstances"}]}, chunks)
    assert check.evidence == f"{BASE}/ec2/client/run_instances.html"


# ------------------------------------------------------------------ the agent

CORPUS = [
    ("s3/client/put_object", "Adds an object to a bucket. Upload a file object to S3."),
    ("ec2/client/run_instances", "Launches the specified number of instances using an AMI."),
    ("sqs/client/send_message", "Delivers a message to the specified queue."),
]


@pytest.fixture
def retriever(tmp_path) -> Retriever:
    store = VectorStore(FakeEmbedder(), persist_dir=str(tmp_path / "chroma"),
                        collection_name="generation_test")
    store.add_chunks([
        DocumentChunk(chunk_id="", content=text, source_url=f"{BASE}/{page}.html",
                      title=page, section="Overview", metadata={}, code_blocks=[],
                      created_at="")
        for page, text in CORPUS
    ])
    return Retriever(store)


def test_catalog_from_store_lists_client_operations(retriever) -> None:
    catalog = DocsCatalog.from_store(retriever.store)
    assert {"s3:putobject", "ec2:runinstances", "sqs:sendmessage"} <= catalog.known


def test_agent_answers_with_sources_and_flags_invented_action(retriever) -> None:
    llm = FakeLLM(reply(["s3:PutObject", "s3:MagicUpload"], "Use put_object [1]."))
    answer = RTFMAgent(retriever, llm).ask("upload a file object to S3", top_k=2)

    assert len(llm.calls) == 1
    assert answer.found and answer.model == "fake/llm"
    assert answer.sources[0].url.endswith("s3/client/put_object.html")
    assert answer.sources[0].cited
    verdicts = {c.action: c.verdict for c in answer.iam_check}
    assert verdicts == {"s3:PutObject": "grounded", "s3:MagicUpload": "not_found"}
    assert any("s3:MagicUpload" in w and "invented" in w for w in answer.warnings)


def test_agent_skips_llm_when_nothing_is_retrieved(retriever) -> None:
    retriever.store.reset()
    llm = FakeLLM("should not be used")
    answer = RTFMAgent(retriever, llm).ask("anything at all")

    assert llm.calls == []
    assert not answer.found and answer.answer == NOT_FOUND


def test_agent_passes_through_not_found(retriever) -> None:
    answer = RTFMAgent(retriever, FakeLLM(NOT_FOUND)).ask("how do I bake bread")
    assert not answer.found and answer.iam_check == []


# --------------------------------------------------------------- LLM factory

def test_get_llm_requires_api_key_for_hosted_providers() -> None:
    for provider in ("openai", "anthropic"):
        cfg = Settings(LLM_PROVIDER=provider, OPENAI_API_KEY=None, ANTHROPIC_API_KEY=None,
                       _env_file=None)
        with pytest.raises(LLMError, match="API_KEY is not set"):
            get_llm(cfg)


def test_get_llm_builds_ollama_without_contacting_it() -> None:
    llm = get_llm(Settings(LLM_PROVIDER="ollama", LLM_MODEL="llama3", _env_file=None))
    assert llm.name == "ollama/llama3"


def test_ollama_unreachable_becomes_llm_error() -> None:
    llm = get_llm(Settings(LLM_PROVIDER="ollama", OLLAMA_HOST="http://127.0.0.1:9",
                           _env_file=None))
    with pytest.raises(LLMError, match="Ollama call failed"):
        llm.complete("system", "user")


# ----------------------------------------------------------------------- API

@pytest.fixture
def client(retriever):
    agent = RTFMAgent(retriever, FakeLLM(reply(["s3:PutObject"], "Use put_object [1].")))
    app.dependency_overrides[query_api.agent_dependency] = lambda: agent
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_query_endpoint_returns_full_answer(client) -> None:
    response = client.post("/api/v1/query", json={"question": "upload a file object to S3", "top_k": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert body["code"].startswith("import boto3")
    assert body["iam_policy"]["Statement"][0]["Action"] == ["s3:PutObject"]
    assert body["iam_check"][0] == {"action": "s3:PutObject", "verdict": "grounded",
                                    "evidence": f"{BASE}/s3/client/put_object.html"}
    assert body["sources"][0]["cited"] is True
    assert len(body["sources"]) == 2


def test_query_endpoint_validates_input(client) -> None:
    assert client.post("/api/v1/query", json={"question": "hi"}).status_code == 422
    assert client.post("/api/v1/query", json={"question": "valid question", "top_k": 50}).status_code == 422


def test_query_endpoint_returns_503_when_llm_unavailable(retriever) -> None:
    class DownLLM(FakeLLM):
        def complete(self, system, user):
            raise LLMError("Ollama call failed: connection refused")

    app.dependency_overrides[query_api.agent_dependency] = lambda: RTFMAgent(retriever, DownLLM())
    try:
        response = TestClient(app).post("/api/v1/query", json={"question": "upload a file"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "connection refused" in response.json()["detail"]
