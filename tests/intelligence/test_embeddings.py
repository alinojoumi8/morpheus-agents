"""Tests for embedding providers."""

import pytest

from intelligence.embeddings import (
    HashEmbeddingProvider,
    cosine_similarity,
    get_embedding_provider,
)


class TestHashEmbeddingProvider:
    def test_embed_returns_correct_dimensions(self):
        provider = HashEmbeddingProvider(dimensions=64)
        emb = provider.embed("hello world")
        assert len(emb) == 64

    def test_embed_is_deterministic(self):
        provider = HashEmbeddingProvider(dimensions=64)
        emb1 = provider.embed("test text")
        emb2 = provider.embed("test text")
        assert emb1 == emb2

    def test_similar_texts_have_higher_similarity(self):
        provider = HashEmbeddingProvider(dimensions=128)
        emb1 = provider.embed("debugging python errors in the code")
        emb2 = provider.embed("debugging python bugs in the code")
        emb3 = provider.embed("cooking a delicious pasta recipe")

        sim_similar = cosine_similarity(emb1, emb2)
        sim_different = cosine_similarity(emb1, emb3)

        # Similar texts should have higher cosine similarity
        assert sim_similar > sim_different

    def test_embed_batch(self):
        provider = HashEmbeddingProvider(dimensions=32)
        texts = ["hello", "world", "test"]
        embeddings = provider.embed_batch(texts)
        assert len(embeddings) == 3
        assert all(len(e) == 32 for e in embeddings)

    def test_embed_empty_text(self):
        provider = HashEmbeddingProvider(dimensions=16)
        emb = provider.embed("")
        assert len(emb) == 16
        # Empty/whitespace text should still produce a vector of the right size
        # (may not be all zeros due to empty-string hash)

    def test_normalized_output(self):
        """Embedding should be L2-normalized."""
        import math
        provider = HashEmbeddingProvider(dimensions=64)
        emb = provider.embed("some meaningful text")
        magnitude = math.sqrt(sum(v * v for v in emb))
        assert abs(magnitude - 1.0) < 0.01  # Should be ~1.0


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 1.0]
        assert abs(cosine_similarity(v, v) - 1.0) < 0.001

    def test_orthogonal_vectors(self):
        v1 = [1.0, 0.0]
        v2 = [0.0, 1.0]
        assert abs(cosine_similarity(v1, v2)) < 0.001

    def test_opposite_vectors(self):
        v1 = [1.0, 0.0]
        v2 = [-1.0, 0.0]
        assert abs(cosine_similarity(v1, v2) - (-1.0)) < 0.001

    def test_different_lengths(self):
        v1 = [1.0, 0.0]
        v2 = [1.0, 0.0, 0.0]
        assert cosine_similarity(v1, v2) == 0.0

    def test_zero_vectors(self):
        v1 = [0.0, 0.0]
        v2 = [1.0, 0.0]
        assert cosine_similarity(v1, v2) == 0.0


class TestGetEmbeddingProvider:
    def test_hash_fallback(self):
        provider = get_embedding_provider(provider_type="hash", dimensions=32)
        assert isinstance(provider, HashEmbeddingProvider)
        emb = provider.embed("test")
        assert len(emb) == 32
