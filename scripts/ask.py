# scripts/ask.py
"""
Ask RTFM a question from the terminal.

    python scripts/ask.py "How do I upload a file to S3?"
    python scripts/ask.py --sources-only "Stop an EC2 instance"   # no LLM call

Uses LLM_PROVIDER / LLM_MODEL etc. from .env.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.rtfm_agent.embedding.embedder import Embedder
from src.rtfm_agent.embedding.vector_store import VectorStore
from src.rtfm_agent.retrieval.retriever import Retriever

MARK = {"grounded": "OK  ", "in_docs": "WARN", "not_found": "FAIL", "too_broad": "WARN"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask RTFM a Boto3 question")
    parser.add_argument("question")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--sources-only", action="store_true",
                        help="show what would be sent to the LLM, without calling it")
    args = parser.parse_args()

    retriever = Retriever(VectorStore(Embedder()))

    if args.sources_only:
        from src.rtfm_agent.generation.prompt import build_prompt
        chunks = retriever.search(args.question, top_k=args.top_k)
        print(build_prompt(args.question, chunks)[1])
        return

    from src.rtfm_agent.generation.agent import RTFMAgent
    from src.rtfm_agent.generation.llm import get_llm

    answer = RTFMAgent(retriever, get_llm()).ask(args.question, top_k=args.top_k)

    print(f"\n{answer.answer}\n")
    if answer.iam_check:
        print("IAM check:")
        for c in answer.iam_check:
            print(f"  [{MARK[c.verdict]}] {c.action:<32} {c.verdict}"
                  + (f"  ({c.evidence})" if c.evidence else ""))
    print("\nSources:")
    for s in answer.sources:
        print(f"  [{s.n}]{'*' if s.cited else ' '} {s.url}")
    for w in answer.warnings:
        print(f"  ! {w}")
    print(f"\n{answer.model} | {answer.seconds}s")


if __name__ == "__main__":
    main()
