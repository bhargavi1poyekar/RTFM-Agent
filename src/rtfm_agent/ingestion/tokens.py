# src/rtfm_agent/ingestion/tokens.py
"""
Token counting with the embedding model's own tokenizer.

Chunk sizes are measured in tokens, not characters: the model reads a fixed
number of tokens, and characters-per-token varies a lot (median ~3.7 on the
Boto3 docs, but ~1.3 on lists of EC2 instance types), so any character limit
is only a guess.
"""
from functools import lru_cache

from config.settings import settings


@lru_cache(maxsize=1)
def _tokenizer():
    from transformers import AutoTokenizer

    name = settings.EMBEDDING_MODEL
    if "/" not in name:  # "all-MiniLM-L6-v2" -> its Hugging Face repo id
        name = f"sentence-transformers/{name}"
    tok = AutoTokenizer.from_pretrained(name)
    # We only count here and never feed the model through this tokenizer, so
    # silence the "sequence longer than max length" warning on long pages.
    tok.model_max_length = 10**9
    return tok


def count_tokens(text: str) -> int:
    """Tokens in `text`, excluding the [CLS]/[SEP] the model adds per input."""
    return len(_tokenizer().encode(text, add_special_tokens=False))
