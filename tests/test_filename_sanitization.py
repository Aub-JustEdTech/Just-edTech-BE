"""
Tests for filename sanitization in conversation report service.
"""

import pytest

from app.services.conversation_report_service import ConversationReportService


def test_sanitize_filename_basic():
    """Test basic filename sanitization."""
    service = ConversationReportService()
    
    # Test simple case
    result = service._sanitize_filename("Hello World")
    assert result == "Hello-World"


def test_sanitize_filename_unicode():
    """Test Unicode character handling."""
    service = ConversationReportService()
    
    # Test non-breaking hyphen (U+2011) - the character causing the original error
    result = service._sanitize_filename("Test\u2011Report")
    assert result == "Test-Report"
    
    # Test various Unicode characters
    result = service._sanitize_filename("Café Report")
    assert result == "Cafe-Report"
    
    result = service._sanitize_filename("Report™")
    assert result == "Report"


def test_sanitize_filename_special_chars():
    """Test special character handling."""
    service = ConversationReportService()
    
    # Test various special characters
    result = service._sanitize_filename("Report@#$%^&*()")
    assert result == "Report"
    
    result = service._sanitize_filename("Report/with\\slashes")
    assert result == "Report-with-slashes"


def test_sanitize_filename_multiple_hyphens():
    """Test multiple consecutive hyphens are collapsed."""
    service = ConversationReportService()
    
    result = service._sanitize_filename("Test---Report")
    assert result == "Test-Report"
    
    result = service._sanitize_filename("Test   Report")
    assert result == "Test-Report"


def test_sanitize_filename_edge_cases():
    """Test edge cases."""
    service = ConversationReportService()
    
    # Test empty string
    result = service._sanitize_filename("")
    assert result == "conversation-report"
    
    # Test only special characters
    result = service._sanitize_filename("@#$%^&*()")
    assert result == "conversation-report"
    
    # Test leading/trailing hyphens
    result = service._sanitize_filename("---Report---")
    assert result == "Report"


def test_sanitize_filename_preserves_extensions():
    """Test that file extensions are preserved."""
    service = ConversationReportService()
    
    result = service._sanitize_filename("My Report.pdf")
    assert result == "My-Report.pdf"
    
    result = service._sanitize_filename("Test\u2011File.txt")
    assert result == "Test-File.txt"


def test_sanitize_filename_real_world_examples():
    """Test real-world conversation titles."""
    service = ConversationReportService()
    
    # Simulate the actual error case
    result = service._sanitize_filename("Conversation‑with‑non‑breaking‑hyphens")
    assert result == "Conversation-with-non-breaking-hyphens"
    
    # Test mixed Unicode and ASCII
    result = service._sanitize_filename("User's Question about Café")
    assert result == "Users-Question-about-Cafe"
    
    # Test emoji (should be removed)
    result = service._sanitize_filename("Great conversation! 🎉")
    assert result == "Great-conversation"
