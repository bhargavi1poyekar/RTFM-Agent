# src/rtfm_agent/embedding/embedder.py
"""
Turns text into vectors with a sentence-transformers model.

The same model must embed both the documents (at index time) and the
user's question (at query time); vectors from different models are not
comparable.
"""
from typing import List, Optional

from config.logging import logger
from config.settings import settings


class Embedder:
    """Thin wrapper around SentenceTransformer, loaded lazily on first use."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        batch_size: Optional[int] = None,
    ):
        self.model_name = model_name or settings.EMBEDDING_MODEL
        self.batch_size = batch_size or settings.EMBEDDING_BATCH_SIZE
        self._model = None

    @property
    def model(self):
        # Imported here so modules that only need the class (and tests that
        # use a fake embedder) don't pay torch's import cost.
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info(f"Loading embedding model: {self.model_name}")
            self._model = SentenceTransformer(self.model_name)
        return self._model

    @property
    def max_tokens(self) -> int:
        """How many tokens the model reads before silently truncating."""
        return self.model.max_seq_length

    def count_tokens(self, text: str) -> int:
        return len(self.model.tokenizer.tokenize(text))

    def embed_documents(
        self, texts: List[str], show_progress: bool = False
    ) -> List[List[float]]:
        """Embed many texts. Vectors are L2-normalised for cosine search."""
        vectors = self.model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=show_progress,
            convert_to_numpy=True,
        )
        return vectors.tolist()

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]
