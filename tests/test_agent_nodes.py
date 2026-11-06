"""Unit tests for LangGraph agent nodes."""
import json
import pytest
from app.agent.nodes import extract_profile, route, ask, build_query, retrieve, answer, State


class TestExtractProfile:
    """Test profile extraction from user messages."""

    def test_extract_complete_profile(self):
        """Test extracting all profile fields."""
        state: State = {
            "question": "Main 25 saal ka hoon, kisan hoon, saalana 3 lakh kamai hai",
            "profile": {},
        }
        result = extract_profile(state)

        assert result["profile"]["age"] == 25
        assert result["profile"]["occupation"] == "kisan"
        assert result["profile"]["annual_income"] == 300000
        assert result["intent"] in ["eligibility", "info"]

    def test_extract_partial_profile(self):
        """Test extracting partial profile."""
        state: State = {
            "question": "Main street vendor hoon",
            "profile": {},
        }
        result = extract_profile(state)

        assert result["profile"]["occupation"] == "street vendor"
        assert "age" not in result["profile"] or result["profile"].get("age") is None

    def test_extract_greeting(self):
        """Test greeting detection."""
        state: State = {
            "question": "Hello! Hi there",
            "profile": {},
        }
        result = extract_profile(state)

        assert result["intent"] == "greeting"

    def test_extract_scheme_specific_query(self):
        """Test scheme-specific query detection."""
        state: State = {
            "question": "Ayushman card kaise banta hai?",
            "profile": {},
        }
        result = extract_profile(state)

        assert result["intent"] == "info"

    def test_preserve_old_profile(self):
        """Test that old profile fields are preserved."""
        old_profile = {"age": 30, "occupation": "driver"}
        state: State = {
            "question": "Meri saalana kamai 2.5 lakh hai",
            "profile": old_profile,
        }
        result = extract_profile(state)

        assert result["profile"]["age"] == 30  # Preserved
        assert result["profile"]["occupation"] == "driver"  # Preserved
        assert result["profile"]["annual_income"] == 250000  # Updated


class TestRoute:
    """Test routing logic."""

    def test_route_to_greet(self):
        """Test greeting routing."""
        state: State = {
            "question": "namaste",
            "profile": {},
            "intent": "greeting",
        }
        result = route(state)
        assert result == "greet"

    def test_route_to_ask_incomplete_profile(self):
        """Test routing to ask when profile incomplete."""
        state: State = {
            "question": "mujhe yojana chahiye",
            "profile": {"age": 25},  # Missing occupation and income
            "intent": "eligibility",
        }
        result = route(state)
        assert result == "ask"

    def test_route_to_retrieve_complete_profile(self):
        """Test routing to retrieve when profile complete."""
        state: State = {
            "question": "mujhe yojana chahiye",
            "profile": {"age": 25, "occupation": "kisan", "annual_income": 300000},
            "intent": "eligibility",
        }
        result = route(state)
        assert result == "retrieve"

    def test_route_direct_scheme_query(self):
        """Test direct scheme queries bypass slot-filling."""
        state: State = {
            "question": "PM-Kisan ke liye kya eligibility hai?",
            "profile": {},  # Empty profile
            "intent": "info",
        }
        result = route(state)
        assert result == "retrieve"  # Should retrieve directly


class TestAsk:
    """Test ask follow-up question generation."""

    def test_ask_missing_age(self):
        """Test asking for missing age."""
        state: State = {
            "question": "mujhe yojana chahiye",
            "profile": {"occupation": "kisan", "annual_income": 300000},
            "topic": "yojana",
        }
        result = ask(state)

        assert "umar" in result["reply"].lower() or "age" in result["reply"].lower()
        assert result["sources"] == []

    def test_ask_missing_occupation(self):
        """Test asking for missing occupation."""
        state: State = {
            "question": "mujhe yojana chahiye",
            "profile": {"age": 25, "annual_income": 300000},
            "topic": "yojana",
        }
        result = ask(state)

        assert "kaam" in result["reply"].lower() or "occupation" in result["reply"].lower()


class TestBuildQuery:
    """Test search query building."""

    def test_build_query_with_hinglish(self):
        """Test building query from Hinglish input."""
        query = build_query(
            "Main 25 saal ka rehri lagata hoon",
            {"age": 25, "occupation": "street vendor"}
        )

        assert isinstance(query, str)
        assert len(query) > 0
        # Query should contain relevant terms
        assert any(word in query.lower() for word in ["street", "vendor", "loan", "rehri"])

    def test_build_query_fallback_on_error(self):
        """Test query building fallback on LLM error."""
        original_topic = "test topic"
        query = build_query(original_topic, {})

        assert query == original_topic  # Fallback to original


class TestRetrieve:
    """Test retrieval logic."""

    def test_retrieve_returns_docs(self):
        """Test that retrieve returns documents."""
        state: State = {
            "question": "mujhe street vendor loan chahiye",
            "profile": {"occupation": "street vendor"},
            "topic": "street vendor loan",
        }
        result = retrieve(state)

        assert "docs" in result
        assert isinstance(result["docs"], list)
        # Should retrieve at least 1 doc
        assert len(result["docs"]) > 0

    def test_retrieve_deduplicates_docs(self):
        """Test that duplicate documents are removed."""
        state: State = {
            "question": "loan",
            "profile": {},
            "topic": "loan",
        }
        result = retrieve(state)

        names = [d["name"] for d in result["docs"]]
        # All doc names should be unique
        assert len(names) == len(set(names))


class TestAnswer:
    """Test answer generation."""

    def test_answer_with_relevant_docs(self):
        """Test generating answer from relevant documents."""
        state: State = {
            "question": "street vendor ke liye kya loan hai?",
            "profile": {"occupation": "street vendor"},
            "docs": [
                {
                    "name": "PM SVANidhi",
                    "text": "PM SVANidhi street vendors ke liye loan...",
                    "url": "https://pmsvanidhi.mohua.gov.in",
                }
            ],
        }
        result = answer(state)

        assert "reply" in result
        assert isinstance(result["reply"], str)
        assert len(result["reply"]) > 0

    def test_answer_respects_used_indices(self):
        """Test that answer only includes used source indices."""
        state: State = {
            "question": "pension yojana?",
            "profile": {"age": 65},
            "docs": [
                {
                    "name": "APY",
                    "text": "APY pension scheme...",
                    "url": "https://atal-pension.gov.in",
                },
                {
                    "name": "PM Mudra",
                    "text": "PM Mudra business loan...",
                    "url": "https://mudra.org.in",
                },
            ],
        }
        result = answer(state)

        # Sources should only contain schemes mentioned in answer
        source_names = [s["name"] for s in result.get("sources", [])]
        assert len(source_names) <= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
