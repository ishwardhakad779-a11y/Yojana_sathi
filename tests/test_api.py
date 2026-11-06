"""API endpoint tests for YojanaSaathi FastAPI application."""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestHealthEndpoints:
    """Test health check endpoints."""

    def test_health_endpoint(self):
        """Test basic health check."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "chal raha hai" in data["status"]

    def test_healthz_endpoint(self):
        """Test deep health check."""
        response = client.get("/healthz")
        assert response.status_code in [200, 503]
        data = response.json()
        assert "status" in data
        assert "database" in data
        assert "vector_store" in data

    def test_healthz_returns_503_on_failure(self):
        """Test that healthz returns 503 if any component fails."""
        response = client.get("/healthz")
        data = response.json()

        if data["status"] == "unhealthy":
            assert response.status_code == 503
        else:
            assert response.status_code == 200


class TestAskEndpoint:
    """Test /ask endpoint."""

    def test_ask_valid_question(self):
        """Test asking a valid question."""
        response = client.post(
            "/ask",
            json={
                "question": "Main 25 saal ka kisan hoon, mujhe kya yojana milegi?",
                "session_id": "test-session-1"
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "answer" in data
        assert "profile" in data
        assert "sources" in data
        assert isinstance(data["answer"], str)
        assert len(data["answer"]) > 0

    def test_ask_greeting(self):
        """Test greeting message."""
        response = client.post(
            "/ask",
            json={
                "question": "Namaste",
                "session_id": "test-session-2"
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "answer" in data
        assert "yojana" in data["answer"].lower()

    def test_ask_scheme_specific_query(self):
        """Test direct scheme query."""
        response = client.post(
            "/ask",
            json={
                "question": "PM-Kisan kya hai?",
                "session_id": "test-session-3"
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "answer" in data
        assert len(data.get("sources", [])) > 0

    def test_ask_empty_question(self):
        """Test that empty questions are rejected."""
        response = client.post(
            "/ask",
            json={
                "question": "   ",
                "session_id": "test-session-4"
            }
        )
        assert response.status_code == 422  # Validation error

    def test_ask_too_long_question(self):
        """Test that overly long questions are rejected."""
        long_question = "x" * 601  # Exceeds 600 char limit
        response = client.post(
            "/ask",
            json={
                "question": long_question,
                "session_id": "test-session-5"
            }
        )
        assert response.status_code == 422

    def test_ask_maintains_session_profile(self):
        """Test that profile is maintained across session."""
        session_id = "test-session-6"

        # First question: provide age
        response1 = client.post(
            "/ask",
            json={
                "question": "Main 30 saal ka hoon",
                "session_id": session_id
            }
        )
        assert response1.status_code == 200

        # Second question: check profile persists
        response2 = client.post(
            "/ask",
            json={
                "question": "Main kisan hoon",
                "session_id": session_id
            }
        )
        assert response2.status_code == 200
        data = response2.json()
        # Profile should now have both age and occupation
        profile = data.get("profile", {})
        assert profile.get("age") == 30 or "age" in str(profile)


class TestRateLimiting:
    """Test rate limiting."""

    def test_rate_limit_enforcement(self):
        """Test that rate limiting kicks in after threshold."""
        session_id = "rate-limit-test"

        # Send 26 requests rapidly (limit is 25/minute)
        responses = []
        for i in range(26):
            response = client.post(
                "/ask",
                json={
                    "question": f"test question {i}",
                    "session_id": session_id
                }
            )
            responses.append(response)

        # At least one should be rate limited
        status_codes = [r.status_code for r in responses]
        assert 429 in status_codes, "Rate limiting not enforced"


class TestHistoryEndpoint:
    """Test conversation history endpoint."""

    def test_history_endpoint(self):
        """Test fetching conversation history."""
        session_id = "history-test-1"

        # Send a question first
        client.post(
            "/ask",
            json={
                "question": "Hello",
                "session_id": session_id
            }
        )

        # Fetch history
        response = client.get(f"/history/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "messages" in data
        assert isinstance(data["messages"], list)

    def test_history_sanitizes_session_id(self):
        """Test that session ID is sanitized."""
        response = client.get("/history/test-session-<script>")
        assert response.status_code == 200
        data = response.json()
        # Script tag should be removed
        assert "<script>" not in data["session_id"]


class TestHomeEndpoint:
    """Test home page endpoint."""

    def test_home_returns_html(self):
        """Test that home returns HTML UI."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")


class TestCORS:
    """Test CORS configuration."""

    def test_cors_headers_present(self):
        """Test that CORS headers are set."""
        response = client.options(
            "/ask",
            headers={"Origin": "http://localhost:3000"}
        )
        # CORS should allow the request
        assert response.status_code in [200, 204]


class TestRequestCorrelation:
    """Test request correlation IDs."""

    def test_request_id_in_response_header(self):
        """Test that X-Request-ID is returned in response."""
        response = client.post(
            "/ask",
            json={
                "question": "test",
                "session_id": "correlation-test"
            }
        )
        assert "X-Request-ID" in response.headers
        assert len(response.headers["X-Request-ID"]) > 0

    def test_custom_request_id_preserved(self):
        """Test that custom X-Request-ID is preserved."""
        custom_id = "custom-req-123"
        response = client.post(
            "/ask",
            json={
                "question": "test",
                "session_id": "correlation-test-2"
            },
            headers={"X-Request-ID": custom_id}
        )
        # Should preserve first 8 chars
        assert response.headers["X-Request-ID"][:8] == custom_id[:8]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
