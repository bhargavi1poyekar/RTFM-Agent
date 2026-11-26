"""
Shared test setup.

Tests must not touch the network. The tokenizer for the embedding model is
loaded from the local Hugging Face cache (it is downloaded once when the
model is first used, e.g. by scripts/build_index.py).
"""
import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
