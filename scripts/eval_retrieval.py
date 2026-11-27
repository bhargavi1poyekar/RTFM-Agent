# scripts/eval_retrieval.py
"""
Score retrieval configurations on the labelled question set.

    python scripts/eval_retrieval.py                 # dev split, all configs
    python scripts/eval_retrieval.py --split test    # held-out split (use sparingly)
    python scripts/eval_retrieval.py --show-misses   # list failed questions

Tune on dev only. Look at test once, at the end, so the reported number is
not something we accidentally optimised for.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.rtfm_agent.embedding.embedder import Embedder
from src.rtfm_agent.embedding.vector_store import VectorStore
from src.rtfm_agent.retrieval.evaluation import evaluate, load_questions
from src.rtfm_agent.retrieval.retriever import Retriever

CONFIGS = {
    "vector (baseline)": dict(mode="vector", one_per_page=False, service_filter=False),
    "vector + 1/page": dict(mode="vector", one_per_page=True, service_filter=False),
    "bm25 + 1/page": dict(mode="bm25", one_per_page=True, service_filter=False),
    "hybrid + 1/page": dict(mode="hybrid", one_per_page=True, service_filter=False),
    "hybrid + 1/page + svc": dict(mode="hybrid", one_per_page=True, service_filter=True),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate retrieval")
    parser.add_argument("--questions", default="eval/retrieval_questions.json")
    parser.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--config", choices=list(CONFIGS), action="append",
                        help="only run these configs (repeatable)")
    parser.add_argument("--show-misses", action="store_true")
    args = parser.parse_args()

    questions = load_questions(args.questions, args.split)
    store = VectorStore(Embedder())
    print(f"{len(questions)} {args.split} questions | {store.count()} chunks indexed\n")
    print(f"{'config':<22} {'Recall@1':>9} {f'Recall@{args.k}':>9} {'MRR':>6}")

    for name in args.config or CONFIGS:
        result = evaluate(Retriever(store, **CONFIGS[name]), questions, k=args.k)
        print(f"{name:<22} {result.recall_at_1:>9.1%} {result.recall_at_k:>9.1%} {result.mrr:>6.3f}")
        if args.show_misses:
            for m in result.misses:
                print(f"    MISS #{m['id']} {m['q']!r}\n"
                      f"      expected {m['expected']}\n      got      {m['got']}")


if __name__ == "__main__":
    main()
