"""Tests for RAG retrieval system."""
import pytest
from app.rag.retriever import search


class TestRetrieval:
    """Test RAG retrieval functionality."""

    def test_search_returns_results(self):
        """Test that search returns documents."""
        results = search("farmer loan scheme", k=3)
        assert isinstance(results, list)
        assert len(results) > 0
        assert len(results) <= 3

    def test_search_result_structure(self):
        """Test that search results have correct structure."""
        results = search("health insurance", k=1)
        assert len(results) > 0

        doc = results[0]
        assert "name" in doc
        assert "text" in doc
        assert "url" in doc
        assert isinstance(doc["name"], str)
        assert isinstance(doc["text"], str)
        assert isinstance(doc["url"], str)

    def test_search_hinglish_keywords(self):
        """Test search with Hinglish keywords."""
        results = search("rehri patri loan", k=5)
        assert len(results) > 0

        # Should return SVANidhi scheme
        names = [d["name"].lower() for d in results]
        assert any("svanidhi" in name for name in names)

    def test_search_farmer_keywords(self):
        """Test farmer-related search."""
        results = search("kisan fasal bima", k=5)
        assert len(results) > 0

        # Should return PM-Kisan or PMFBY
        names = [d["name"].lower() for d in results]
        assert any("kisan" in name or "fasal" in name or "pmfby" in name for name in names)

    def test_search_pension_keywords(self):
        """Test pension-related search."""
        results = search("budhapa pension old age", k=5)
        assert len(results) > 0

        names = [d["name"].lower() for d in results]
        assert any("pension" in name or "apy" in name or "atal" in name for name in names)

    def test_search_handles_empty_query(self):
        """Test that search handles empty queries gracefully."""
        results = search("", k=3)
        # Should still return some results or empty list
        assert isinstance(results, list)

    def test_search_limit_respected(self):
        """Test that k parameter limits results."""
        results = search("loan scheme", k=2)
        assert len(results) <= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
