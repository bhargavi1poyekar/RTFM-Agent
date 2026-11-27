# src/rtfm_agent/generation/agent.py
"""
The full question -> answer pipeline:

    retrieve top-k chunks -> build prompt -> LLM -> parse -> check IAM policy

The LLM is not called at all when retrieval finds nothing.
"""
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config.logging import logger
from config.settings import settings
from src.rtfm_agent.generation.iam_check import ActionCheck, DocsCatalog, check_policy
from src.rtfm_agent.generation.llm import LLM
from src.rtfm_agent.generation.prompt import NOT_FOUND, build_prompt, parse_answer


@dataclass
class Source:
    n: int
    title: str
    url: str
    cited: bool


@dataclass
class Answer:
    question: str
    answer: str
    found: bool
    code: Optional[str] = None
    iam_policy: Optional[Dict] = None
    iam_check: List[ActionCheck] = field(default_factory=list)
    sources: List[Source] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    model: str = ""
    seconds: float = 0.0


class RTFMAgent:
    def __init__(self, retriever, llm: LLM, catalog: Optional[DocsCatalog] = None):
        self.retriever = retriever
        self.llm = llm
        self._catalog = catalog

    @property
    def catalog(self) -> DocsCatalog:
        """Built on first use from the whole library (takes a few seconds)."""
        if self._catalog is None:
            self._catalog = DocsCatalog.from_store(self.retriever.store)
        return self._catalog

    def ask(self, question: str, top_k: Optional[int] = None) -> Answer:
        start = time.perf_counter()
        chunks = self.retriever.search(question, top_k=top_k or settings.RETRIEVAL_TOP_K)
        if not chunks:
            return Answer(question, NOT_FOUND, found=False, model=self.llm.name,
                          seconds=time.perf_counter() - start)

        system, user = build_prompt(question, chunks)
        parsed = parse_answer(self.llm.complete(system, user), len(chunks))

        answer = Answer(
            question=question,
            answer=parsed.text,
            found=parsed.found,
            code=parsed.code,
            iam_policy=parsed.policy,
            warnings=list(parsed.warnings),
            model=self.llm.name,
            sources=[
                Source(i, c.metadata.get("title", ""), c.metadata.get("source_url", ""),
                       cited=i in parsed.cited)
                for i, c in enumerate(chunks, start=1)
            ],
        )

        if parsed.policy is not None:
            answer.iam_check = check_policy(parsed.policy, chunks, self.catalog)
            for c in answer.iam_check:
                if c.verdict == "not_found":
                    answer.warnings.append(f"IAM action {c.action} isn't in the docs; it may be invented.")
                elif c.verdict == "in_docs":
                    answer.warnings.append(f"IAM action {c.action} exists but isn't in the retrieved pages.")
                elif c.verdict == "too_broad":
                    answer.warnings.append(f"IAM action {c.action} is a wildcard; narrow it down.")

        answer.seconds = round(time.perf_counter() - start, 2)
        logger.info("Answered question", extra={
            "model": answer.model, "found": answer.found, "seconds": answer.seconds,
            "n_warnings": len(answer.warnings),
        })
        return answer
