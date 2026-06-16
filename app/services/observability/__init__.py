"""
Observability and tracing initialization for LangSmith.
"""

import logging
import os

from app.core.config import settings

logger = logging.getLogger(__name__)

_initialized = False


def initialize_langsmith():
    """
    Initialize LangSmith tracing by setting environment variables.
    
    This should be called early in the application startup to ensure
    all LangSmith wrappers and decorators work correctly.
    Safe to call multiple times — only runs once.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    if settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
        os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
        
        logger.info(
            f"LangSmith tracing initialized for project: {settings.LANGSMITH_PROJECT}"
        )
    else:
        logger.info(
            "LangSmith tracing is disabled. "
            "Set LANGSMITH_TRACING=true and LANGSMITH_API_KEY in .env to enable."
        )
