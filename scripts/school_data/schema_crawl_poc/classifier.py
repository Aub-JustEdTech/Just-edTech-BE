"""LLM page classifier for the schema-driven crawler POC.

Canonical implementation now lives in
app/services/web_scraper/page_classifier.py. This module re-exports PageClassifier
so existing POC imports keep working without drift.
"""

from app.services.web_scraper.page_classifier import (  # noqa: F401
    PageClassifier,
)
