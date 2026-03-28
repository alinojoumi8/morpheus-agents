"""
Embedding provider abstraction for the intelligence module.

Supports multiple backends:
- OpenAI API (text-embedding-3-small, text-embedding-ada-002)
- Ollama (local, e.g. nomic-embed-text)
- Sentence-transformers (local, pure Python)
- Fallback: simple TF-IDF-like hashing for basic similarity without any dependencies

The provider is selected based on config and available dependencies.
"""

import hashlib
import logging
import math
import os
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

# Singleton cache for loaded models
_SENTENCE_TRANSFORMER_MODEL = None
_PROVIDER_CACHE = {}


class EmbeddingProvider:
    """Abstract embedding provider."""

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        raise NotImplementedError

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        return [self.embed(t) for t in texts]

    @property
    def name(self) -> str:
        return self.__class__.__name__


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Embeddings via OpenAI-compatible API (works with OpenAI, OpenRouter, etc.)."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        dimensions: int = 384,
    ):
        super().__init__(dimensions)
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url

    def _get_client(self):
        from openai import OpenAI
        kwargs = {}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        if self.api_key:
            kwargs["api_key"] = self.api_key
        return OpenAI(**kwargs)

    def embed(self, text: str) -> List[float]:
        client = self._get_client()
        # Truncate to avoid token limits
        text = text[:8000]
        try:
            response = client.embeddings.create(
                model=self.model,
                input=text,
                dimensions=self.dimensions,
            )
            return response.data[0].embedding
        except Exception as exc:
            logger.warning("OpenAI embedding failed: %s", exc)
            # Fall back to hash-based embedding
            return HashEmbeddingProvider(self.dimensions).embed(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        client = self._get_client()
        truncated = [t[:8000] for t in texts]
        try:
            response = client.embeddings.create(
                model=self.model,
                input=truncated,
                dimensions=self.dimensions,
            )
            return [item.embedding for item in response.data]
        except Exception:
            return [self.embed(t) for t in texts]


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embeddings via local Ollama server."""

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
        dimensions: int = 384,
    ):
        super().__init__(dimensions)
        self.model = model
        self.base_url = base_url.rstrip("/")

    def embed(self, text: str) -> List[float]:
        import httpx
        try:
            response = httpx.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text[:8000]},
                timeout=30.0,
            )
            response.raise_for_status()
            embedding = response.json()["embedding"]
            # Truncate or pad to target dimensions
            if len(embedding) > self.dimensions:
                embedding = embedding[:self.dimensions]
            elif len(embedding) < self.dimensions:
                embedding.extend([0.0] * (self.dimensions - len(embedding)))
            return embedding
        except Exception as exc:
            logger.warning("Ollama embedding failed: %s", exc)
            return HashEmbeddingProvider(self.dimensions).embed(text)


class SentenceTransformerProvider(EmbeddingProvider):
    """Embeddings via sentence-transformers (local, no API needed)."""

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        dimensions: int = 384,
    ):
        super().__init__(dimensions)
        self.model_name = model_name

    def _get_model(self):
        global _SENTENCE_TRANSFORMER_MODEL
        if _SENTENCE_TRANSFORMER_MODEL is None:
            from sentence_transformers import SentenceTransformer
            _SENTENCE_TRANSFORMER_MODEL = SentenceTransformer(self.model_name)
        return _SENTENCE_TRANSFORMER_MODEL

    def embed(self, text: str) -> List[float]:
        try:
            model = self._get_model()
            embedding = model.encode(text[:8000], show_progress_bar=False)
            result = embedding.tolist()
            if len(result) > self.dimensions:
                result = result[:self.dimensions]
            elif len(result) < self.dimensions:
                result.extend([0.0] * (self.dimensions - len(result)))
            return result
        except Exception as exc:
            logger.warning("Sentence-transformer embedding failed: %s", exc)
            return HashEmbeddingProvider(self.dimensions).embed(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        try:
            model = self._get_model()
            truncated = [t[:8000] for t in texts]
            embeddings = model.encode(truncated, show_progress_bar=False)
            results = []
            for emb in embeddings:
                result = emb.tolist()
                if len(result) > self.dimensions:
                    result = result[:self.dimensions]
                elif len(result) < self.dimensions:
                    result.extend([0.0] * (self.dimensions - len(result)))
                results.append(result)
            return results
        except Exception:
            return [self.embed(t) for t in texts]


class HashEmbeddingProvider(EmbeddingProvider):
    """Fallback: deterministic hash-based pseudo-embeddings.

    Not semantically meaningful but provides consistent vectors for
    exact/near-duplicate detection when no real embedding model is available.
    Uses character n-gram hashing to create sparse feature vectors.
    """

    def __init__(self, dimensions: int = 384):
        super().__init__(dimensions)

    def embed(self, text: str) -> List[float]:
        text = text.lower().strip()
        # Create vector from character trigram hashes
        vector = [0.0] * self.dimensions
        words = re.split(r'\s+', text)

        for word in words:
            # Word-level hash
            h = int(hashlib.md5(word.encode()).hexdigest(), 16)
            idx = h % self.dimensions
            vector[idx] += 1.0

            # Character trigrams for fuzzy matching
            for i in range(len(word) - 2):
                trigram = word[i:i+3]
                h = int(hashlib.md5(trigram.encode()).hexdigest(), 16)
                idx = h % self.dimensions
                vector[idx] += 0.5

        # L2 normalize
        magnitude = math.sqrt(sum(v * v for v in vector))
        if magnitude > 0:
            vector = [v / magnitude for v in vector]

        return vector


def get_embedding_provider(
    provider_type: str = "auto",
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    dimensions: int = 384,
) -> EmbeddingProvider:
    """Factory: create the best available embedding provider.

    provider_type: "auto", "openai", "ollama", "local", "hash"
    """
    cache_key = f"{provider_type}:{model}:{base_url}:{dimensions}"
    if cache_key in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[cache_key]

    provider = None

    if provider_type == "openai" or (provider_type == "auto" and os.getenv("OPENAI_API_KEY")):
        try:
            from openai import OpenAI  # noqa: F401
            provider = OpenAIEmbeddingProvider(
                model=model or "text-embedding-3-small",
                api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
                base_url=base_url or None,
                dimensions=dimensions,
            )
            logger.info("Using OpenAI embedding provider (model=%s)", provider.model)
        except ImportError:
            pass

    if provider is None and provider_type in ("ollama", "auto"):
        try:
            import httpx
            # Quick check if Ollama is running
            ollama_url = base_url or "http://localhost:11434"
            resp = httpx.get(f"{ollama_url}/api/tags", timeout=2.0)
            if resp.status_code == 200:
                provider = OllamaEmbeddingProvider(
                    model=model or "nomic-embed-text",
                    base_url=ollama_url,
                    dimensions=dimensions,
                )
                logger.info("Using Ollama embedding provider (model=%s)", provider.model)
        except Exception:
            pass

    if provider is None and provider_type in ("local", "auto"):
        try:
            from sentence_transformers import SentenceTransformer  # noqa: F401
            provider = SentenceTransformerProvider(
                model_name=model or "all-MiniLM-L6-v2",
                dimensions=dimensions,
            )
            logger.info("Using sentence-transformers embedding provider")
        except ImportError:
            pass

    if provider is None:
        provider = HashEmbeddingProvider(dimensions=dimensions)
        if provider_type != "hash":
            logger.info("No embedding provider available, using hash fallback. "
                        "Install openai, or run Ollama, or install sentence-transformers "
                        "for semantic search.")

    _PROVIDER_CACHE[cache_key] = provider
    return provider


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
