# scripts/eval_generation.py
"""
Score full answers (retrieval + LLM) on the labelled questions.

    python scripts/eval_generation.py --model llama3.2:3b --limit 12
    python scripts/eval_generation.py --model llama3.1:8b --limit 12 --out results.json

Uses the dev split by default (see eval_retrieval.py for why test is kept
aside). For each answer it checks:
- code:        a python block that parses (ast), and that calls the method
               of one of the question's expected pages (e.g. upload_file)
- policy:      a valid-JSON IAM policy, and the IAM check verdicts
- citations:   cites at least one expected page; doesn't just cite everything
"""
import argparse
import ast
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config.settings import settings
from src.rtfm_agent.embedding.embedder import Embedder
from src.rtfm_agent.embedding.vector_store import VectorStore
from src.rtfm_agent.generation.agent import RTFMAgent
from src.rtfm_agent.generation.llm import get_llm
from src.rtfm_agent.retrieval.evaluation import load_questions
from src.rtfm_agent.retrieval.retriever import Retriever, page_key


def score(answer, expected_pages):
    code_ok = False
    if answer.code:
        try:
            ast.parse(answer.code)
            code_ok = True
        except SyntaxError:
            pass
    methods = {p.split("/")[-1] for p in expected_pages}
    right_method = bool(answer.code) and any(f".{m}(" in answer.code for m in methods)

    cited_pages = {page_key(s.url) for s in answer.sources if s.cited}
    verdicts = Counter(c.verdict for c in answer.iam_check)
    all_grounded = bool(answer.iam_check) and set(verdicts) == {"grounded"}
    cites_right = bool(cited_pages & set(expected_pages))
    return {
        "found": answer.found,
        "code_parses": code_ok,
        "right_method": right_method,
        "has_policy": answer.iam_policy is not None,
        "policy_grounded": all_grounded,
        "cites_right_page": cites_right,
        "fully_good": right_method and cites_right and all_grounded,
        "cites_everything": len(answer.sources) > 1 and len(cited_pages) == len(answer.sources),
        "verdicts": dict(verdicts),
        "seconds": answer.seconds,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate generated answers")
    parser.add_argument("--model", default=settings.LLM_MODEL)
    parser.add_argument("--questions", default="eval/retrieval_questions.json")
    parser.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default=None, help="save every answer + score as JSON")
    parser.add_argument("--persist-dir", default=None,
                        help="index folder (default: CHROMADB_PERSIST_DIR)")
    parser.add_argument("--top-k", type=int, default=None)
    args = parser.parse_args()

    cfg = settings.model_copy(update={"LLM_MODEL": args.model})
    store = VectorStore(Embedder(), persist_dir=args.persist_dir)
    agent = RTFMAgent(Retriever(store), get_llm(cfg))
    questions = load_questions(args.questions, args.split)[: args.limit]

    agent.llm.complete("Reply with OK.", "OK?")  # load the model before timing
    _ = agent.catalog

    rows = []
    for i, q in enumerate(questions, 1):
        answer = agent.ask(q.q, top_k=args.top_k)
        s = score(answer, q.pages)
        rows.append({"id": q.id, "q": q.q, "expected": q.pages, "score": s,
                     "answer": answer.answer, "warnings": answer.warnings,
                     "iam_check": [vars(c) for c in answer.iam_check]})
        flags = " ".join(k for k in ("code_parses", "right_method", "has_policy", "cites_right_page")
                         if s[k])
        print(f"[{i}/{len(questions)}] #{q.id} {s['seconds']:>5.1f}s  {flags}  {s['verdicts']}",
              flush=True)

    n = len(rows)
    pct = lambda key: sum(r["score"][key] for r in rows) / n
    verdicts = sum((Counter(r["score"]["verdicts"]) for r in rows), Counter())
    secs = sorted(r["score"]["seconds"] for r in rows)
    print(f"\n{agent.llm.name} on {n} {args.split} questions")
    for key in ("found", "code_parses", "right_method", "has_policy", "policy_grounded",
                "cites_right_page", "cites_everything", "fully_good"):
        print(f"  {key:<18} {pct(key):>6.0%}")
    print(f"  IAM actions        {dict(verdicts)}")
    print(f"  seconds            median {secs[n // 2]:.0f}, max {secs[-1]:.0f}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)


if __name__ == "__main__":
    main()
