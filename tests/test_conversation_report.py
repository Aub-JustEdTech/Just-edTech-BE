"""
Tests for the conversation report endpoint.
"""

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_conversation_report_requires_auth():
    """Report endpoint should require authentication."""
    response = client.get("/api/v1/conversations/1/report")
    assert response.status_code in (401, 403)

