# src/rtfm_agent/api/query.py
"""POST /api/v1/query: ask a Boto3 question, get code + IAM policy + sources."""
from functools import lru_cache
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.rtfm_agent.generation.agent import RTFMAgent
from src.rtfm_agent.generation.llm import LLMError, get_llm

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000,
                          examples=["How do I upload a file to an S3 bucket?"])
    top_k: Optional[int] = Field(None, ge=1, le=10)


class SourceOut(BaseModel):
    n: int
    title: str
    url: str
    cited: bool


class ActionCheckOut(BaseModel):
    action: str
    verdict: str
    evidence: Optional[str] = None


class QueryResponse(BaseModel):
    question: str
    answer: str
    found: bool
    code: Optional[str] = None
    iam_policy: Optional[Dict] = None
    iam_check: List[ActionCheckOut] = []
    sources: List[SourceOut] = []
    warnings: List[str] = []
    model: str
    seconds: float


@lru_cache(maxsize=1)
def get_agent() -> RTFMAgent:
    """Built once, on the first request (loads the embedding model + index)."""
    from src.rtfm_agent.embedding.embedder import Embedder
    from src.rtfm_agent.embedding.vector_store import VectorStore
    from src.rtfm_agent.retrieval.retriever import Retriever

    return RTFMAgent(Retriever(VectorStore(Embedder())), get_llm())


def agent_dependency() -> RTFMAgent:
    try:
        return get_agent()
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, agent: RTFMAgent = Depends(agent_dependency)) -> QueryResponse:
    # Plain `def`: FastAPI runs it in a worker thread, so a slow LLM call
    # doesn't block other requests.
    try:
        answer = agent.ask(req.question, top_k=req.top_k)
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return QueryResponse.model_validate(answer, from_attributes=True)
